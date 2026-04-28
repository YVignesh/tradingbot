"""
strategies/ema_crossover.py — EMA 9/21 Crossover Strategy (Long + Short)
=========================================================================
Signal logic (bidirectional, intraday):
  EMA(fast) > EMA(slow) → BUY (if FLAT) or COVER (if SHORT)
  EMA(fast) < EMA(slow) → SELL (if LONG) or SHORT (if FLAT)
  TSL triggered          → SELL / COVER immediately (highest priority)
  Past squareoff time    → SELL / COVER forced exit

Direction state machine:
  FLAT → BUY → LONG → SELL → FLAT
  FLAT → SHORT → SHORT → COVER → FLAT

Config keys used (from config.json):
  strategy : symbol, exchange, interval, ema_fast, ema_slow
  risk     : capital, max_risk_pct, sl_points, tp_points, max_qty, trailing_sl
  broker   : product, squareoff_time
  bot      : dry_run
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional

from strategies.base import BaseStrategy
from indicators.trend import ema
from indicators.volatility import atr as compute_atr
from broker.market_data import get_candles_n_days, candles_to_dataframe
from broker.instruments import InstrumentMaster
from risk.trailing_sl import TrailingSL
from utils import get_logger

_log = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


class EmaCrossoverStrategy(BaseStrategy):
    """
    EMA crossover strategy — bidirectional long + short.
    Position state is updated exclusively through on_fill().

    on_fill() routing:
      BUY  fill + FLAT  → open LONG
      BUY  fill + SHORT → cover SHORT (close)
      SELL fill + LONG  → close LONG
      SELL fill + FLAT  → open SHORT
    """

    NAME = "ema_crossover"

    def __init__(self, config: dict):
        super().__init__(config)

        strat  = config["strategy"]
        risk   = config["risk"]
        broker = config["broker"]
        bot    = config["bot"]

        self.symbol   = strat["symbol"]
        self.exchange = strat["exchange"]
        self.interval = strat["interval"]
        self.fast     = int(strat.get("ema_fast", 9))
        self.slow     = int(strat.get("ema_slow", 21))

        self.capital      = float(risk["capital"])
        self.max_risk_pct = float(risk["max_risk_pct"])
        self.sl_points    = float(risk["sl_points"])
        self.tp_points    = float(risk["tp_points"])
        self.max_qty      = int(risk["max_qty"])

        self.product        = broker["product"]
        self.squareoff_time = broker.get("squareoff_time", "15:15")
        self.dry_run        = bool(bot["dry_run"])

        self.token: Optional[str] = None

        # Position state — updated exclusively through on_fill()
        self.direction: str       = "FLAT"   # "LONG" | "SHORT" | "FLAT"
        self.entry_price: float   = 0.0
        self.entry_qty:   int     = 0
        self.entry_order_id: Optional[str] = None

        self._ltp: float = 0.0

        # Trailing SL
        tsl_cfg = risk.get("trailing_sl", {})
        self.tsl_enabled    = bool(tsl_cfg.get("enabled", False))
        self._atr_period    = int(tsl_cfg.get("atr_period", 14))
        self._last_atr      = 0.0
        self._tsl_triggered = False   # written by on_tick (WS thread), read by generate_signal
        self.tsl: TrailingSL | None = None
        if self.tsl_enabled:
            self.tsl = TrailingSL(
                mode           = str(tsl_cfg.get("mode", "points")),
                value          = float(tsl_cfg.get("value", 5.0)),
                activation_gap = float(tsl_cfg.get("activation_gap", 0.0)),
            )

    @property
    def in_position(self) -> bool:
        return self.direction != "FLAT"

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def on_start(self, session) -> None:
        master = InstrumentMaster()
        master.load()
        self.token = master.get_token(self.exchange, self.symbol)
        if not self.token:
            raise ValueError(
                f"Symbol {self.symbol!r} not found on {self.exchange}. "
                "Check symbol and exchange in config.json."
            )
        tsl_desc = (
            f"{self.tsl._mode}:{self.tsl._value}  gap={self.tsl._activation_gap}"
            if self.tsl_enabled and self.tsl else "off"
        )
        _log.info(
            "EmaCrossover ready: %s (token=%s)  EMA%d/EMA%d  %s  %s  tsl=%s",
            self.symbol, self.token, self.fast, self.slow,
            self.interval, "DRY_RUN" if self.dry_run else "LIVE", tsl_desc,
        )

    def on_stop(self) -> None:
        if self.direction != "FLAT":
            _log.warning(
                "Stopped with open %s position: %s  qty=%d  entry=₹%.2f",
                self.direction, self.symbol, self.entry_qty, self.entry_price,
            )

    # ── Tick handler ─────────────────────────────────────────────────────────

    def on_tick(self, tick: dict) -> None:
        ltp = tick.get("ltp", self._ltp)
        self._ltp = ltp
        # TSL check runs in the WebSocket thread; sets a flag read by
        # generate_signal() on the next loop iteration (CPython GIL safe).
        if self.tsl_enabled and self.direction != "FLAT" and self.tsl is not None:
            if self.tsl.update(ltp):
                self._tsl_triggered = True

    # ── Signal generation ─────────────────────────────────────────────────────

    def generate_signal(self, session) -> Optional[str]:
        """
        Returns 'BUY', 'SELL', 'SHORT', 'COVER', or None.

        Uses the EMA state on the last COMPLETED bar (iloc[-2]) to decide
        direction — not a one-bar crossover event. This means:
          - After a SELL (going FLAT), SHORT fires on the very next loop
            iteration if EMA is still bearish.
          - After a COVER (going FLAT), BUY fires on the next iteration
            if EMA is still bullish.
        """
        # Priority 1: TSL — exit at market, do not wait for candle
        if self._tsl_triggered and self.direction != "FLAT":
            signal = "COVER" if self.direction == "SHORT" else "SELL"
            _log.warning("TSL triggered — immediate %s signal", signal)
            return signal

        # Priority 2: forced exit past squareoff time
        if self.direction != "FLAT" and self._past_squareoff():
            signal = "COVER" if self.direction == "SHORT" else "SELL"
            _log.info("Past squareoff (%s IST) — forcing %s", self.squareoff_time, signal)
            return signal

        df = self._fetch_candles(session)
        if df is None:
            return None

        if len(df) < self.slow + 2:
            _log.warning("Not enough candles (%d) — need %d", len(df), self.slow + 2)
            return None

        df["ema_fast"] = ema(df["close"], self.fast)
        df["ema_slow"] = ema(df["close"], self.slow)

        # Cache ATR for TSL arming at entry (only needed in atr mode)
        if self.tsl_enabled and self.tsl is not None and self.tsl._mode == "atr":
            atr_s          = compute_atr(df["high"], df["low"], df["close"], self._atr_period)
            self._last_atr = float(atr_s.iloc[-2]) if len(atr_s) >= 2 else 0.0

        last_fast   = df["ema_fast"].iloc[-2]
        last_slow   = df["ema_slow"].iloc[-2]
        last_close  = df["close"].iloc[-2]
        ema_bullish = last_fast > last_slow

        _log.info(
            "%s  close=₹%.2f  EMA%d=%.2f  EMA%d=%.2f  trend=%s  position=%s",
            self.symbol, last_close,
            self.fast, last_fast, self.slow, last_slow,
            "BULL" if ema_bullish else "BEAR",
            self.direction,
        )

        # Priority 3: EMA state-based signals
        if ema_bullish:
            if self.direction == "SHORT":
                return "COVER"
            if self.direction == "FLAT":
                return "BUY"
        else:  # bearish
            if self.direction == "LONG":
                return "SELL"
            if self.direction == "FLAT":
                return "SHORT"

        return None

    # ── Fill handler ─────────────────────────────────────────────────────────

    def on_fill(self, order_update: dict) -> None:
        if str(order_update.get("status", "")).lower() != "complete":
            return

        txn   = str(order_update.get("transactiontype", "")).upper()
        price = float(order_update.get("averageprice", 0) or 0)
        qty   = int(order_update.get("filledshares",  0) or 0)
        oid   = str(order_update.get("uniqueorderid", ""))

        if txn == "BUY":
            if self.direction == "FLAT":
                self._open_long(price, qty, oid)
            elif self.direction == "SHORT":
                self._close_short(price, qty, oid)

        elif txn == "SELL":
            if self.direction == "LONG":
                self._close_long(price, qty, oid)
            elif self.direction == "FLAT":
                self._open_short(price, qty, oid)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def compute_qty(self, ltp: float) -> int:
        risk_amount = self.capital * self.max_risk_pct / 100.0
        qty = int(risk_amount / self.sl_points)
        return max(1, min(qty, self.max_qty))

    def get_state(self) -> dict:
        if self.direction == "LONG" and self._ltp > 0:
            unrealised = (self._ltp - self.entry_price) * self.entry_qty
        elif self.direction == "SHORT" and self._ltp > 0:
            unrealised = (self.entry_price - self._ltp) * self.entry_qty
        else:
            unrealised = 0.0

        return {
            "in_position":    self.direction != "FLAT",
            "direction":      self.direction,
            "entry_price":    self.entry_price,
            "entry_qty":      self.entry_qty,
            "ltp":            self._ltp,
            "unrealised_pnl": unrealised,
            "tsl_sl":         self.tsl.current_sl  if self.tsl_enabled and self.tsl else 0.0,
            "tsl_activated":  self.tsl.is_activated if self.tsl_enabled and self.tsl else False,
        }

    # ── Private fill helpers ──────────────────────────────────────────────────

    def _open_long(self, price: float, qty: int, oid: str) -> None:
        self.direction      = "LONG"
        self.entry_price    = price
        self.entry_qty      = qty
        self.entry_order_id = oid
        self._tsl_triggered = False
        tsl_status = self._arm_tsl(price, "long")
        _log.info("LONG OPENED:  %s  qty=%d  entry=₹%.2f  order=%s  tsl=%s",
                  self.symbol, qty, price, oid, tsl_status)

    def _close_long(self, price: float, qty: int, oid: str) -> None:
        pnl = (price - self.entry_price) * self.entry_qty
        _log.info("LONG CLOSED:  %s  qty=%d  exit=₹%.2f  entry=₹%.2f  P&L=₹%.2f  order=%s",
                  self.symbol, qty, price, self.entry_price, pnl, oid)
        self._reset_position()

    def _open_short(self, price: float, qty: int, oid: str) -> None:
        self.direction      = "SHORT"
        self.entry_price    = price
        self.entry_qty      = qty
        self.entry_order_id = oid
        self._tsl_triggered = False
        tsl_status = self._arm_tsl(price, "short")
        _log.info("SHORT OPENED: %s  qty=%d  entry=₹%.2f  order=%s  tsl=%s",
                  self.symbol, qty, price, oid, tsl_status)

    def _close_short(self, price: float, qty: int, oid: str) -> None:
        pnl = (self.entry_price - price) * self.entry_qty
        _log.info("SHORT COVERED: %s  qty=%d  cover=₹%.2f  entry=₹%.2f  P&L=₹%.2f  order=%s",
                  self.symbol, qty, price, self.entry_price, pnl, oid)
        self._reset_position()

    def _arm_tsl(self, price: float, direction: str) -> str:
        """Arm TSL if enabled; skip silently when ATR mode has no ATR yet."""
        if not (self.tsl_enabled and self.tsl is not None):
            return "off"
        if self.tsl._mode == "atr" and self._last_atr <= 0:
            _log.warning("TSL mode=atr but no ATR available yet — TSL not armed for this trade")
            return "no-atr"
        self.tsl.arm(price, direction=direction, atr=self._last_atr)
        return "armed"

    def _reset_position(self) -> None:
        self.direction      = "FLAT"
        self.entry_price    = 0.0
        self.entry_qty      = 0
        self.entry_order_id = None
        self._tsl_triggered = False
        if self.tsl_enabled and self.tsl is not None:
            self.tsl.reset()

    def _past_squareoff(self) -> bool:
        now = datetime.now(IST)
        h, m = map(int, self.squareoff_time.split(":"))
        return now >= now.replace(hour=h, minute=m, second=0, microsecond=0)

    def _fetch_candles(self, session):
        try:
            days = max(5, (self.slow * 3) // 75 + 2)
            candles = get_candles_n_days(
                session, self.exchange, self.token,
                days=days, interval=self.interval,
            )
            if not candles:
                _log.warning("No candles returned for %s", self.symbol)
                return None
            return candles_to_dataframe(candles)
        except Exception as e:
            _log.warning("Candle fetch failed: %s", e)
            return None
