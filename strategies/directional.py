"""
strategies/directional.py — Shared directional strategy plumbing
=================================================================
Common state machine for long/short strategies that trade from
completed candle signals and update live state from order fills.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

from broker.instruments import InstrumentMaster
from broker.market_data import candles_to_dataframe, get_candles_n_days
from indicators.volatility import atr as compute_atr
from risk.trailing_sl import TrailingSL
from strategies.base import BaseStrategy
from utils import get_logger

IST = timezone(timedelta(hours=5, minutes=30))


class DirectionalStrategy(BaseStrategy):
    NAME = "directional"

    def __init__(self, config: dict):
        super().__init__(config)

        strat = config["strategy"]
        risk = config["risk"]
        broker = config["broker"]
        bot = config["bot"]

        self.symbol = str(strat["symbol"]).upper()
        self.exchange = str(strat["exchange"]).upper()
        self.interval = str(strat.get("interval", "FIVE_MINUTE")).upper()
        self.product = str(broker["product"]).upper()
        self.squareoff_time = broker.get("squareoff_time", "15:15")
        self.dry_run = bool(bot["dry_run"])

        self.capital = float(risk["capital"])
        self.max_risk_pct = float(risk["max_risk_pct"])
        self.sl_points = float(risk.get("sl_points", 0))
        self.tp_points = float(risk.get("tp_points", 0))
        self.max_qty = int(risk["max_qty"])

        # ATR-based dynamic SL/TP (overrides fixed sl_points/tp_points when set)
        self.sl_atr_multiplier = float(risk.get("sl_atr_multiplier", 0))
        self.tp_atr_multiplier = float(risk.get("tp_atr_multiplier", 0))

        self.strategy_name = str(strat.get("name", self.NAME))
        self.charge_segment = str(strat.get("charge_segment") or broker.get("charge_segment") or "").strip()

        self.token: Optional[str] = None
        self.direction = "FLAT"
        self.entry_price = 0.0
        self.entry_qty = 0
        self.entry_order_id: Optional[str] = None
        self._ltp = 0.0

        tsl_cfg = risk.get("trailing_sl", {})
        self.tsl_enabled = bool(tsl_cfg.get("enabled", False))
        self._atr_period = int(tsl_cfg.get("atr_period", 14))
        self._last_atr = 0.0
        self._tsl_triggered = False
        self.tsl: TrailingSL | None = None
        if self.tsl_enabled:
            self.tsl = TrailingSL(
                mode=str(tsl_cfg.get("mode", "points")),
                value=float(tsl_cfg.get("value", 5.0)),
                activation_gap=float(tsl_cfg.get("activation_gap", 0.0)),
            )

        self._active_trade: Optional[dict] = None
        self._completed_trades: list[dict] = []
        self.log = get_logger(f"strategy.{self.symbol}.{self.strategy_name}")

    @property
    def in_position(self) -> bool:
        return self.direction != "FLAT"

    def required_history_bars(self) -> int:
        raise NotImplementedError

    def prepare_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError

    def signal_from_prepared(self, df: pd.DataFrame, index: int, direction: str) -> Optional[str]:
        raise NotImplementedError

    def describe_bar(self, df: pd.DataFrame, index: int) -> str:
        close = float(df["close"].iloc[index])
        return f"close=₹{close:.2f}"

    def on_start(self, session) -> None:
        master = InstrumentMaster()
        master.load()
        resolved = master.resolve_symbol(self.exchange, self.symbol)
        if resolved and resolved != self.symbol:
            self.log.info("Symbol resolved: %s → %s", self.symbol, resolved)
            self.symbol = resolved
        self.token = master.get_token(self.exchange, self.symbol)
        if not self.token:
            raise ValueError(
                f"Symbol {self.symbol!r} not found on {self.exchange}. "
                "Check symbol and exchange in config.json."
            )
        tsl_desc = (
            f"{self.tsl._mode}:{self.tsl._value} gap={self.tsl._activation_gap}"
            if self.tsl_enabled and self.tsl else "off"
        )
        self.log.info(
            "%s ready token=%s interval=%s mode=%s tsl=%s",
            self.__class__.__name__,
            self.token,
            self.interval,
            "DRY_RUN" if self.dry_run else "LIVE",
            tsl_desc,
        )

    def on_stop(self) -> None:
        if self.direction != "FLAT":
            self.log.warning(
                "Stopped with open %s position: %s qty=%d entry=₹%.2f",
                self.direction,
                self.symbol,
                self.entry_qty,
                self.entry_price,
            )

    def on_tick(self, tick: dict) -> None:
        ltp = float(tick.get("ltp", self._ltp) or self._ltp)
        self._ltp = ltp
        # Track MAE/MFE intra-trade extremes
        if self._active_trade is not None and ltp > 0:
            if ltp > self._active_trade["high_since_entry"]:
                self._active_trade["high_since_entry"] = ltp
            if ltp < self._active_trade["low_since_entry"]:
                self._active_trade["low_since_entry"] = ltp
        if self.tsl_enabled and self.direction != "FLAT" and self.tsl is not None:
            if self.tsl.update(ltp):
                self._tsl_triggered = True

    def generate_signal(self, session) -> Optional[str]:
        if self._tsl_triggered and self.direction != "FLAT":
            signal = "COVER" if self.direction == "SHORT" else "SELL"
            self.log.warning("TSL triggered - immediate %s signal", signal)
            return signal

        if self.direction != "FLAT" and self._past_squareoff():
            signal = "COVER" if self.direction == "SHORT" else "SELL"
            self.log.info("Past squareoff (%s IST) - forcing %s", self.squareoff_time, signal)
            return signal

        df = self._fetch_candles(session)
        if df is None:
            return None

        prepared = self.prepare_dataframe(df)
        min_bars = self.required_history_bars()
        if len(prepared) < min_bars:
            self.log.warning("Not enough candles (%d) - need %d", len(prepared), min_bars)
            return None

        idx = len(prepared) - 2
        # Always cache ATR for dynamic SL/TP and TSL
        self._cache_atr(prepared, idx)

        signal = self.signal_from_prepared(prepared, idx, self.direction)
        self.log.info(
            "%s %s position=%s",
            self.symbol,
            self.describe_bar(prepared, idx),
            self.direction,
        )
        return signal

    def on_fill(self, order_update: dict) -> None:
        if str(order_update.get("status", "")).lower() != "complete":
            return

        txn = str(order_update.get("transactiontype", "")).upper()
        price = float(order_update.get("averageprice", 0) or 0)
        qty = int(order_update.get("filledshares", 0) or 0)
        oid = str(order_update.get("uniqueorderid", ""))
        filled_at = order_update.get("filled_at")

        if qty <= 0 or txn not in {"BUY", "SELL"}:
            return

        if txn == "BUY":
            if self.direction == "FLAT":
                self._open_long(price, qty, oid, filled_at)
            elif self.direction == "LONG":
                self._increase_long(price, qty, oid)
            elif self.direction == "SHORT":
                self._reduce_short(price, qty, oid, filled_at)
        elif txn == "SELL":
            if self.direction == "LONG":
                self._reduce_long(price, qty, oid, filled_at)
            elif self.direction == "FLAT":
                self._open_short(price, qty, oid, filled_at)
            elif self.direction == "SHORT":
                self._increase_short(price, qty, oid)

    def effective_sl_points(self) -> float:
        """Return ATR-based SL distance if configured, else fixed sl_points."""
        if self.sl_atr_multiplier > 0 and self._last_atr > 0:
            return round(self._last_atr * self.sl_atr_multiplier, 2)
        return self.sl_points

    def effective_tp_points(self) -> float:
        """Return ATR-based TP distance if configured, else fixed tp_points."""
        if self.tp_atr_multiplier > 0 and self._last_atr > 0:
            return round(self._last_atr * self.tp_atr_multiplier, 2)
        return self.tp_points

    def get_state(self) -> dict:
        if self.direction == "LONG" and self._ltp > 0:
            unrealised = (self._ltp - self.entry_price) * self.entry_qty
        elif self.direction == "SHORT" and self._ltp > 0:
            unrealised = (self.entry_price - self._ltp) * self.entry_qty
        else:
            unrealised = 0.0

        return {
            "in_position": self.direction != "FLAT",
            "direction": self.direction,
            "entry_price": self.entry_price,
            "entry_qty": self.entry_qty,
            "ltp": self._ltp,
            "unrealised_pnl": unrealised,
            "tsl_sl": self.tsl.current_sl if self.tsl_enabled and self.tsl else 0.0,
            "tsl_activated": self.tsl.is_activated if self.tsl_enabled and self.tsl else False,
        }

    def pop_completed_trades(self) -> list[dict]:
        trades = list(self._completed_trades)
        self._completed_trades.clear()
        return trades

    def recover_position(
        self,
        direction: str,
        qty: int,
        entry_price: float,
        recovered_at: Optional[datetime] = None,
        order_id: str = "RECOVERED",
    ) -> None:
        direction = direction.upper()
        if direction not in {"LONG", "SHORT"} or qty <= 0 or entry_price <= 0:
            return

        self.direction = direction
        self.entry_price = entry_price
        self.entry_qty = qty
        self.entry_order_id = order_id
        self._ltp = entry_price
        self._tsl_triggered = False
        self._active_trade = {
            "strategy": self.strategy_name,
            "symbol": self.symbol,
            "exchange": self.exchange,
            "direction": direction,
            "entry_time": recovered_at or datetime.now(IST),
            "entry_turnover": entry_price * qty,
            "entry_qty": qty,
            "exit_turnover": 0.0,
            "realized_qty": 0,
            "gross_pnl": 0.0,
            "recovered": True,
            "high_since_entry": entry_price,
            "low_since_entry": entry_price,
        }
        tsl_status = self._arm_tsl(entry_price, "long" if direction == "LONG" else "short")
        self.log.warning(
            "Recovered %s %s qty=%d entry=₹%.2f tsl=%s",
            direction,
            self.symbol,
            qty,
            entry_price,
            tsl_status,
        )

    def _open_long(self, price: float, qty: int, oid: str, filled_at: object = None) -> None:
        self.direction = "LONG"
        self.entry_price = price
        self.entry_qty = qty
        self.entry_order_id = oid
        self._ltp = price
        self._tsl_triggered = False
        self._active_trade = {
            "strategy": self.strategy_name,
            "symbol": self.symbol,
            "exchange": self.exchange,
            "direction": "LONG",
            "entry_time": self._parse_fill_time(filled_at),
            "entry_turnover": price * qty,
            "entry_qty": qty,
            "exit_turnover": 0.0,
            "realized_qty": 0,
            "gross_pnl": 0.0,
            "recovered": False,
            "high_since_entry": price,
            "low_since_entry": price,
        }
        tsl_status = self._arm_tsl(price, "long")
        self.log.info(
            "LONG OPENED: %s qty=%d entry=₹%.2f order=%s tsl=%s",
            self.symbol,
            qty,
            price,
            oid,
            tsl_status,
        )

    def _increase_long(self, price: float, qty: int, oid: str) -> None:
        total_qty = self.entry_qty + qty
        if total_qty <= 0:
            return
        self.entry_price = ((self.entry_price * self.entry_qty) + (price * qty)) / total_qty
        self.entry_qty = total_qty
        self.entry_order_id = oid
        if self._active_trade is not None:
            self._active_trade["entry_turnover"] += price * qty
            self._active_trade["entry_qty"] += qty
        self.log.info(
            "LONG INCREASED: %s add_qty=%d avg_entry=₹%.2f total_qty=%d order=%s",
            self.symbol,
            qty,
            self.entry_price,
            self.entry_qty,
            oid,
        )

    def _reduce_long(self, price: float, qty: int, oid: str, filled_at: object = None) -> None:
        exit_qty = min(qty, self.entry_qty)
        pnl = (price - self.entry_price) * exit_qty
        remaining = self.entry_qty - exit_qty
        self._record_trade_exit(price, exit_qty, pnl, filled_at)
        if remaining <= 0:
            self.log.info(
                "LONG CLOSED: %s qty=%d exit=₹%.2f entry=₹%.2f P&L=₹%.2f order=%s",
                self.symbol,
                exit_qty,
                price,
                self.entry_price,
                pnl,
                oid,
            )
            self._reset_position()
            return
        self.entry_qty = remaining
        self.log.info(
            "LONG REDUCED: %s exit_qty=%d exit=₹%.2f realised=₹%.2f remaining_qty=%d order=%s",
            self.symbol,
            exit_qty,
            price,
            pnl,
            self.entry_qty,
            oid,
        )

    def _open_short(self, price: float, qty: int, oid: str, filled_at: object = None) -> None:
        self.direction = "SHORT"
        self.entry_price = price
        self.entry_qty = qty
        self.entry_order_id = oid
        self._ltp = price
        self._tsl_triggered = False
        self._active_trade = {
            "strategy": self.strategy_name,
            "symbol": self.symbol,
            "exchange": self.exchange,
            "direction": "SHORT",
            "entry_time": self._parse_fill_time(filled_at),
            "entry_turnover": price * qty,
            "entry_qty": qty,
            "exit_turnover": 0.0,
            "realized_qty": 0,
            "gross_pnl": 0.0,
            "recovered": False,
            "high_since_entry": price,
            "low_since_entry": price,
        }
        tsl_status = self._arm_tsl(price, "short")
        self.log.info(
            "SHORT OPENED: %s qty=%d entry=₹%.2f order=%s tsl=%s",
            self.symbol,
            qty,
            price,
            oid,
            tsl_status,
        )

    def _increase_short(self, price: float, qty: int, oid: str) -> None:
        total_qty = self.entry_qty + qty
        if total_qty <= 0:
            return
        self.entry_price = ((self.entry_price * self.entry_qty) + (price * qty)) / total_qty
        self.entry_qty = total_qty
        self.entry_order_id = oid
        if self._active_trade is not None:
            self._active_trade["entry_turnover"] += price * qty
            self._active_trade["entry_qty"] += qty
        self.log.info(
            "SHORT INCREASED: %s add_qty=%d avg_entry=₹%.2f total_qty=%d order=%s",
            self.symbol,
            qty,
            self.entry_price,
            self.entry_qty,
            oid,
        )

    def _reduce_short(self, price: float, qty: int, oid: str, filled_at: object = None) -> None:
        cover_qty = min(qty, self.entry_qty)
        pnl = (self.entry_price - price) * cover_qty
        remaining = self.entry_qty - cover_qty
        self._record_trade_exit(price, cover_qty, pnl, filled_at)
        if remaining <= 0:
            self.log.info(
                "SHORT COVERED: %s qty=%d cover=₹%.2f entry=₹%.2f P&L=₹%.2f order=%s",
                self.symbol,
                cover_qty,
                price,
                self.entry_price,
                pnl,
                oid,
            )
            self._reset_position()
            return
        self.entry_qty = remaining
        self.log.info(
            "SHORT REDUCED: %s cover_qty=%d cover=₹%.2f realised=₹%.2f remaining_qty=%d order=%s",
            self.symbol,
            cover_qty,
            price,
            pnl,
            self.entry_qty,
            oid,
        )

    def _record_trade_exit(self, price: float, qty: int, pnl: float, filled_at: object = None) -> None:
        if self._active_trade is None or qty <= 0:
            return
        self._active_trade["exit_turnover"] += price * qty
        self._active_trade["realized_qty"] += qty
        self._active_trade["gross_pnl"] += pnl
        if self.entry_qty - qty > 0:
            return

        total_qty = int(self._active_trade["entry_qty"])
        avg_entry = self._active_trade["entry_turnover"] / total_qty if total_qty > 0 else self.entry_price
        avg_exit = self._active_trade["exit_turnover"] / total_qty if total_qty > 0 else price
        direction = self._active_trade["direction"]
        high_since = self._active_trade.get("high_since_entry", avg_entry)
        low_since = self._active_trade.get("low_since_entry", avg_entry)
        if direction == "LONG":
            mae = round(avg_entry - low_since, 2)   # max drawdown from entry
            mfe = round(high_since - avg_entry, 2)   # max run-up from entry
        else:
            mae = round(high_since - avg_entry, 2)   # max adverse for shorts
            mfe = round(avg_entry - low_since, 2)   # max favourable for shorts
        self._completed_trades.append({
            "strategy": self._active_trade["strategy"],
            "symbol": self._active_trade["symbol"],
            "exchange": self._active_trade["exchange"],
            "direction": direction,
            "entry_time": self._active_trade["entry_time"],
            "exit_time": self._parse_fill_time(filled_at),
            "entry_price": round(avg_entry, 2),
            "exit_price": round(avg_exit, 2),
            "qty": total_qty,
            "gross_pnl": round(self._active_trade["gross_pnl"], 2),
            "recovered": bool(self._active_trade.get("recovered", False)),
            "mae": mae,
            "mfe": mfe,
        })
        self._active_trade = None

    def _arm_tsl(self, price: float, direction: str) -> str:
        if not (self.tsl_enabled and self.tsl is not None):
            return "off"
        if self.tsl._mode == "atr" and self._last_atr <= 0:
            self.log.warning("TSL mode=atr but no ATR available yet - TSL not armed for this trade")
            return "no-atr"
        self.tsl.arm(price, direction=direction, atr=self._last_atr)
        return "armed"

    def _reset_position(self) -> None:
        self.direction = "FLAT"
        self.entry_price = 0.0
        self.entry_qty = 0
        self.entry_order_id = None
        self._tsl_triggered = False
        if self.tsl_enabled and self.tsl is not None:
            self.tsl.reset()

    def _cache_atr(self, df: pd.DataFrame, index: int) -> None:
        atr_series = df["atr"] if "atr" in df.columns else compute_atr(
            df["high"], df["low"], df["close"], self._atr_period
        )
        if len(atr_series) <= abs(index):
            self._last_atr = 0.0
            return
        value = atr_series.iloc[index]
        self._last_atr = float(value) if pd.notna(value) else 0.0

    def _past_squareoff(self) -> bool:
        now = datetime.now(IST)
        hour, minute = map(int, self.squareoff_time.split(":"))
        return now >= now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def _fetch_candles(self, session):
        try:
            bars = max(self.required_history_bars(), 60)
            days = max(5, (bars * 3) // 75 + 2)
            candles = get_candles_n_days(
                session,
                self.exchange,
                self.token,
                days=days,
                interval=self.interval,
            )
            if not candles:
                self.log.warning("No candles returned for %s", self.symbol)
                return None
            return candles_to_dataframe(candles)
        except Exception as exc:
            self.log.warning("Candle fetch failed: %s", exc)
            return None

    def _parse_fill_time(self, value: object) -> datetime:
        if isinstance(value, datetime):
            return value.astimezone(IST) if value.tzinfo else value.replace(tzinfo=IST)
        if isinstance(value, str) and value.strip():
            text = value.strip()
            for fmt in ("%Y-%m-%d %H:%M:%S", "%d-%b-%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
                try:
                    parsed = datetime.strptime(text, fmt)
                    return parsed.astimezone(IST) if parsed.tzinfo else parsed.replace(tzinfo=IST)
                except ValueError:
                    continue
        return datetime.now(IST)
