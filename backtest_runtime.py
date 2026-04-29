"""
backtest_runtime.py - Generic backtester for directional strategies
===================================================================
Day-by-day simulation
---------------------
Bars from all symbols are processed in chronological order together.
On each trading day:
  1. Walk-forward screener selects top-N symbols for that day.
  2. All selected symbols share daily risk counters.
  3. A shared capital pool funds all positions (sizing = pool / n_active).
  4. Open positions are force-closed at end of each day (intraday model).

Warmup pre-fetch
----------------
Each strategy declares required_history_bars(). The backtester automatically
fetches that many extra bars BEFORE the user's --from date so that all
indicators are fully warmed up by the first actual trading day.

Journal
-------
Every backtest writes a detailed day-by-day log to data/journal/.
Format: screener selection + scores, per-bar signals + indicators,
trade entries/exits, end-of-day P&L. Use this to analyse and fine-tune.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

import pandas as pd

from allocation.registry import get_allocator
from broker.charges import Segment, calculate_charges
from broker.instruments import InstrumentMaster
from broker.market_data import candles_to_dataframe, get_candles
from broker.session import AngelSession
from indicators.volatility import atr as compute_atr
from risk.trailing_sl import TrailingSL
from screener.registry import get_screener
from strategies.registry import STRATEGIES
from utils import get_logger

_log = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

# Approximate intraday bars per trading day for each interval
_BARS_PER_DAY: dict[str, int] = {
    "ONE_MINUTE": 375,
    "THREE_MINUTE": 125,
    "FIVE_MINUTE": 75,
    "TEN_MINUTE": 37,
    "FIFTEEN_MINUTE": 25,
    "THIRTY_MINUTE": 12,
    "ONE_HOUR": 6,
    "ONE_DAY": 1,
}


# ─────────────────────────────────────────────────────────────────────────────
# Journal
# ─────────────────────────────────────────────────────────────────────────────

class BacktestJournal:
    """
    Writes a structured day-by-day backtest log to a text file.
    Trade entry/exit and day-summary lines are also echoed to the console.
    Bar-by-bar indicator lines go to the file only (too verbose for console).
    """

    SEP_MAJOR = "=" * 80
    SEP_MINOR = "─" * 80

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(path, "w", encoding="utf-8")
        self._path = path
        self._day_trades: list[dict] = []
        self._day_pool_start: float = 0.0

    # ── low-level write ───────────────────────────────────────────────────────

    def _w(self, line: str = "") -> None:
        self._f.write(line + "\n")

    def _echo(self, line: str = "") -> None:
        self._f.write(line + "\n")
        print(line)

    # ── session header ────────────────────────────────────────────────────────

    def log_header(
        self,
        strategy: str,
        from_date: str,
        to_date: str,
        capital: float,
        interval: str,
        top_n: int,
        sl: float,
        tp: float,
        tsl_desc: str,
        warmup_bars: int,
        warmup_days: int,
    ) -> None:
        now = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
        self._echo(self.SEP_MAJOR)
        self._echo(f"  BACKTEST SESSION  |  {strategy}  |  {from_date} → {to_date}")
        self._echo(f"  Capital: Rs{capital:,.0f}  |  Interval: {interval}  |  Top-N: {top_n}")
        self._echo(f"  SL: {sl} pts  |  TP: {tp} pts  |  {tsl_desc}")
        self._echo(
            f"  Warmup: {warmup_bars} bars → {warmup_days} extra days fetched before start date"
        )
        self._echo(f"  Journal: {self._path}")
        self._echo(f"  Created: {now}")
        self._echo(self.SEP_MAJOR)

    # ── day header ────────────────────────────────────────────────────────────

    def log_day_header(self, trade_date: date, day_num: int, pool: float) -> None:
        self._day_trades = []
        self._day_pool_start = pool
        label = trade_date.strftime("%A")
        self._echo("")
        self._echo(self.SEP_MAJOR)
        self._echo(
            f"  DAY {day_num}  |  {trade_date}  ({label})  |  Pool: Rs{pool:,.2f}"
        )
        self._echo(self.SEP_MAJOR)

    # ── screener ──────────────────────────────────────────────────────────────

    def log_screener(
        self,
        picks: list[dict],
        pool: float,
        alloc_map: dict[str, float],
    ) -> None:
        n = len(picks)
        self._echo("")
        self._echo(f"  [SCREENER]  Walk-forward selection  |  Active: {n}  |  Pool: Rs{pool:,.0f}")
        self._echo(
            f"  {'Rank':<4}  {'Symbol':<16}  {'Score':>6}  {'Close':>8}  {'ATR':>6}  "
            f"{'Mom5d':>7}  {'VolSpike':>9}  {'Gap%':>5}  {'Alloc':>10}"
        )
        self._echo(f"  {self.SEP_MINOR}")
        for pick in picks:
            alloc = alloc_map.get(pick["symbol"], 0.0)
            self._echo(
                f"  {pick['rank']:<4}  {pick['symbol']:<16}  {pick['score']:>6.2f}  "
                f"Rs{pick['close']:>7.1f}  {pick['atr']:>6.2f}  "
                f"{pick['momentum_5d']:>+6.2f}%  {pick['volume_spike']:>8.2f}x  "
                f"{pick['gap_pct']:>5.2f}%  Rs{alloc:>8,.0f}"
            )
        self._echo(f"  {self.SEP_MINOR}")

    def log_no_screener(self, symbols: list[str], pool: float) -> None:
        alloc = pool / max(len(symbols), 1)
        self._echo("")
        self._echo(
            f"  [SYMBOLS]  {', '.join(symbols)}  |  Alloc: Rs{alloc:,.0f}/symbol"
        )

    # ── bar ───────────────────────────────────────────────────────────────────

    def log_bar(
        self,
        ts: Any,
        symbol: str,
        bar_open: float,
        bar_high: float,
        bar_low: float,
        bar_close: float,
        indicators: dict[str, float],
        signal: str,
        direction: str,
    ) -> None:
        time_str = ts.astimezone(IST).strftime("%H:%M")
        ind_str = "  ".join(f"{k}:{v:.2f}" for k, v in list(indicators.items())[:6])
        pos_tag = f"[{direction}]" if direction != "FLAT" else ""
        self._w(
            f"  {time_str}  {symbol:<16} {pos_tag:<7} "
            f"O:{bar_open:.2f} H:{bar_high:.2f} L:{bar_low:.2f} C:{bar_close:.2f} "
            f"| {ind_str} | → {signal}"
        )

    # ── trade events ──────────────────────────────────────────────────────────

    def log_entry(
        self,
        ts: Any,
        symbol: str,
        direction: str,
        price: float,
        qty: int,
        sl: float,
        tp: float,
        alloc: float,
    ) -> None:
        time_str = ts.astimezone(IST).strftime("%H:%M")
        line = (
            f"  ▶ ENTRY {direction:<5}  {time_str}  {symbol:<16} "
            f"Price:Rs{price:.2f}  Qty:{qty}  SL:Rs{sl:.2f}  TP:Rs{tp:.2f}  "
            f"Alloc:Rs{alloc:,.0f}"
        )
        self._echo(line)

    def log_exit(
        self,
        ts: Any,
        symbol: str,
        direction: str,
        price: float,
        qty: int,
        reason: str,
        gross: float,
        charges: float,
        net: float,
        pool: float,
    ) -> None:
        time_str = ts.astimezone(IST).strftime("%H:%M")
        sign = "+" if net >= 0 else ""
        line = (
            f"  ◀ EXIT  {reason:<10}  {time_str}  {symbol:<16} "
            f"Price:Rs{price:.2f}  Qty:{qty}  "
            f"Gross:{sign}Rs{gross:.2f}  Charges:Rs{charges:.2f}  Net:{sign}Rs{net:.2f}  "
            f"Pool:Rs{pool:,.2f}"
        )
        self._echo(line)
        self._day_trades.append(
            {"symbol": symbol, "direction": direction, "net": net, "reason": reason}
        )

    # ── end-of-day summary ────────────────────────────────────────────────────

    def log_day_end(
        self,
        trade_date: date,
        pool: float,
        selected_symbols: list[str],
        sym_pnl: dict[str, float],
        sym_trades: dict[str, int],
    ) -> None:
        day_net = pool - self._day_pool_start
        winners = sum(1 for t in self._day_trades if t["net"] > 0)
        losers = sum(1 for t in self._day_trades if t["net"] < 0)
        sign = "+" if day_net >= 0 else ""
        self._echo("")
        self._echo(f"  {self.SEP_MINOR}")
        self._echo(f"  END OF DAY {trade_date}  |  Trades:{len(self._day_trades)}  W:{winners}  L:{losers}")
        self._echo(
            f"  Day net P&L: {sign}Rs{day_net:.2f}  |  Pool: Rs{pool:,.2f}  (was Rs{self._day_pool_start:,.2f})"
        )
        self._echo(f"  {self.SEP_MINOR}")
        self._echo(f"  Per-symbol:")
        for sym in selected_symbols:
            pnl = sym_pnl.get(sym, 0.0)
            ntrades = sym_trades.get(sym, 0)
            sign2 = "+" if pnl >= 0 else ""
            self._echo(f"    {sym:<18}  {ntrades:>2} trade(s)  {sign2}Rs{pnl:.2f}")

    # ── aggregate ─────────────────────────────────────────────────────────────

    def log_aggregate(
        self,
        results: list[dict],
        capital: float,
        pool: float,
        strategy: str,
    ) -> None:
        all_trades = [t for r in results for t in r["trades"]]
        total_net = pool - capital
        total_gross = sum(t["gross_pnl"] for t in all_trades)
        total_charges = sum(t["charges"] for t in all_trades)
        winners = sum(1 for t in all_trades if t["net_pnl"] > 0)
        wr = winners / len(all_trades) * 100 if all_trades else 0
        ret = total_net / capital * 100
        sign = "+" if total_net >= 0 else ""
        self._echo("")
        self._echo(self.SEP_MAJOR)
        self._echo(f"  FINAL AGGREGATE  |  {strategy}")
        self._echo(self.SEP_MAJOR)
        self._echo(f"  Opening capital    : Rs{capital:>12,.2f}")
        self._echo(f"  Final pool         : Rs{pool:>12,.2f}")
        self._echo(f"  Total return       : {sign}{ret:>+.2f}%")
        self._echo(f"  Total net P&L      : {sign}Rs{total_net:>+,.2f}")
        self._echo(f"  Total gross P&L    : Rs{total_gross:>+,.2f}")
        self._echo(f"  Total charges      : Rs{total_charges:>,.2f}")
        self._echo(f"  Total trades       : {len(all_trades)}")
        self._echo(f"  Win rate           : {wr:.1f}%")
        self._echo(self.SEP_MINOR)
        self._echo(f"  Per-symbol (sorted by net P&L):")
        for r in sorted(results, key=lambda x: sum(t["net_pnl"] for t in x["trades"]), reverse=True):
            sp = sum(t["net_pnl"] for t in r["trades"])
            st = len(r["trades"])
            sw = sum(1 for t in r["trades"] if t["net_pnl"] > 0)
            swr = sw / st * 100 if st else 0
            s2 = "+" if sp >= 0 else ""
            self._echo(
                f"    {r['symbol']:<18}  {st:>4} trades  WR={swr:.0f}%  {s2}Rs{sp:>+,.2f}"
            )
        self._echo(self.SEP_MAJOR)

    def close(self) -> None:
        self._f.close()


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Angel One strategy backtester. All settings come from config.json.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python backtest.py --from 2026-01-01 --to 2026-03-31\n"
            "\n"
            "Optional overrides (normally set in config.json):\n"
            "  --strategy  --symbols  --interval  --capital  --no-tsl\n"
        ),
    )
    parser.add_argument("--from", dest="from_date", required=True, metavar="YYYY-MM-DD")
    parser.add_argument("--to",   dest="to_date",   required=True, metavar="YYYY-MM-DD")
    parser.add_argument("--config",   default="config.json",  help="Config file path (default: config.json)")
    parser.add_argument("--strategy", default=None,           help="Override strategy name from config")
    parser.add_argument("--interval", default=None,           help="Override candle interval from config")
    parser.add_argument("--symbols",  default=None,           help="Override symbols (comma-separated), disables screener")
    parser.add_argument("--capital",  type=float, default=None, help="Override capital from config")
    parser.add_argument("--no-tsl",   action="store_true",    help="Disable trailing stop-loss")
    return parser.parse_args()


def _load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _base_strategy_template(config: dict) -> dict:
    if "strategy" in config and isinstance(config["strategy"], dict):
        return copy.deepcopy(config["strategy"])
    strategies = config.get("strategies", [])
    if strategies:
        return copy.deepcopy(strategies[0])
    raise KeyError("No strategy config found")


def _looks_like_option(symbol: str) -> bool:
    return bool(re.search(r"(CE|PE)(?:[-_ ]|$)", symbol.upper()))


def _resolve_trade_segment(strat_cfg: dict, broker_cfg: dict) -> str:
    configured = strat_cfg.get("charge_segment") or broker_cfg.get("charge_segment")
    if configured:
        return configured
    exchange = str(strat_cfg.get("exchange", "NSE")).upper()
    symbol = str(strat_cfg.get("symbol", "")).upper()
    product = str(broker_cfg.get("product", "INTRADAY")).upper()
    if exchange in {"NSE", "BSE"}:
        return Segment.EQUITY_INTRADAY if product == "INTRADAY" else Segment.EQUITY_DELIVERY
    if exchange == "NFO":
        return Segment.EQUITY_OPTIONS if _looks_like_option(symbol) else Segment.EQUITY_FUTURES
    if exchange == "CDS":
        return Segment.CURRENCY_OPTIONS if _looks_like_option(symbol) else Segment.CURRENCY_FUTURES
    if exchange in {"MCX", "NCDEX"}:
        return Segment.COMMODITY_OPTIONS if _looks_like_option(symbol) else Segment.COMMODITY_FUTURES
    return Segment.EQUITY_INTRADAY


# ─────────────────────────────────────────────────────────────────────────────
# Data fetching
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_all_candles(
    session,
    exchange: str,
    token: str,
    interval: str,
    from_date: str,
    to_date: str,
) -> pd.DataFrame:
    fmt = "%Y-%m-%d %H:%M"
    start = datetime.strptime(from_date, fmt).replace(tzinfo=IST)
    end = datetime.strptime(to_date, fmt).replace(tzinfo=IST)

    all_candles: list[dict] = []
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=60), end)
        from_str = cursor.strftime(fmt)
        to_str = chunk_end.strftime(fmt)
        _log.info("Fetching candles %s -> %s", from_str, to_str)
        try:
            batch = get_candles(session, exchange, token, interval, from_str, to_str)
            all_candles.extend(batch)
        except Exception as exc:
            _log.warning("Candle fetch failed for chunk %s->%s: %s", from_str, to_str, exc)
        cursor = chunk_end + timedelta(minutes=1)

    if not all_candles:
        raise RuntimeError("No candles returned for the requested date range.")

    df = candles_to_dataframe(all_candles)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    _log.info("Total candles fetched: %d", len(df))
    return df


def _load_watchlist_symbols(screener_cfg: dict, default_exchange: str = "NSE") -> list[dict]:
    from screener.universe import load_universe
    return load_universe(screener_cfg.get("watchlist", []), default_exchange=default_exchange)


def _warmup_extra_days(interval: str, warmup_bars: int) -> int:
    bars_per_day = _BARS_PER_DAY.get(interval, 25)
    return int(math.ceil(warmup_bars / bars_per_day * 1.5)) + 3


# ─────────────────────────────────────────────────────────────────────────────
# Walk-forward screener
# ─────────────────────────────────────────────────────────────────────────────

def _compute_screener_selection_per_day(
    daily_dfs: dict[str, pd.DataFrame],
    screener_cfg: dict,
    screener,
    backtest_start: date,
    backtest_end: date,
) -> tuple[dict[str, set], dict[date, list[dict]]]:
    """
    Walk-forward screener. Returns:
      selected  : {symbol: set[date]} — which days each symbol is selected
      daily_picks: {date: [ranked pick dicts]} — scores & metrics per day for journal
    """
    top_n = int(screener_cfg.get("top_n", 5))
    lookback = int(screener_cfg.get("lookback_days", 45))
    min_price = float(screener_cfg.get("min_price", 0.0))
    max_price = float(screener_cfg.get("max_price", 0.0))
    min_avg_vol = float(screener_cfg.get("min_avg_volume", 0.0))
    min_atr_val = float(screener_cfg.get("min_atr", 0.0))
    max_atr_val = float(screener_cfg.get("max_atr", 0.0))
    max_gap = float(screener_cfg.get("max_gap_pct", 0.0))

    all_trade_dates: set[date] = set()
    for df in daily_dfs.values():
        for ts in df.index:
            d = ts.astimezone(IST).date()
            if backtest_start <= d <= backtest_end:
                all_trade_dates.add(d)

    selected: dict[str, set] = {sym: set() for sym in daily_dfs}
    daily_picks: dict[date, list[dict]] = {}

    for trade_date in sorted(all_trade_dates):
        cutoff = datetime(trade_date.year, trade_date.month, trade_date.day, tzinfo=IST)
        candidates = []

        for symbol, df in daily_dfs.items():
            hist = df[df.index < cutoff].tail(lookback + 10)
            if len(hist) < 25:
                continue

            close = float(hist["close"].iloc[-1])
            avg_vol_s = hist["volume"].rolling(20, min_periods=20).mean()
            avg_volume = float(avg_vol_s.iloc[-1]) if not pd.isna(avg_vol_s.iloc[-1]) else 0.0
            atr_s = compute_atr(hist["high"], hist["low"], hist["close"], 14)
            atr_val = float(atr_s.iloc[-1]) if not pd.isna(atr_s.iloc[-1]) else 0.0
            prev_5 = float(hist["close"].iloc[-6]) if len(hist) >= 6 else close
            prev_1 = float(hist["close"].iloc[-2]) if len(hist) >= 2 else close

            momentum_5d = ((close / prev_5) - 1.0) * 100 if prev_5 > 0 else 0.0
            volume_spike = float(hist["volume"].iloc[-1]) / avg_volume if avg_volume > 0 else 0.0
            gap_pct = abs((close / prev_1) - 1.0) * 100 if prev_1 > 0 else 0.0

            if min_price > 0 and close < min_price:
                continue
            if max_price > 0 and close > max_price:
                continue
            if min_avg_vol > 0 and avg_volume < min_avg_vol:
                continue
            if min_atr_val > 0 and atr_val < min_atr_val:
                continue
            if max_atr_val > 0 and atr_val > max_atr_val:
                continue
            if max_gap > 0 and gap_pct > max_gap:
                continue

            base = {
                "symbol": symbol,
                "close": close,
                "atr": round(atr_val, 4),
                "momentum_5d": round(momentum_5d, 4),
                "volume_spike": round(volume_spike, 4),
                "gap_pct": round(gap_pct, 4),
            }
            base.update(screener.extra_metrics(hist))
            if not screener.passes_filter(base):
                continue
            candidates.append(base)

        picks = screener.rank(candidates, top_n)
        for cand in picks:
            selected[cand["symbol"]].add(trade_date)

        daily_picks[trade_date] = picks

    total_slots = sum(len(v) for v in selected.values())
    _log.info(
        "Screener walk-forward: %d trading days, avg %.1f symbols/day",
        len(all_trade_dates),
        total_slots / max(len(all_trade_dates), 1),
    )
    return selected, daily_picks


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _trade_record(
    entry_time, exit_time, entry_price, exit_price, qty,
    gross_pnl, charges, exit_reason, capital_after, direction,
) -> dict:
    return {
        "entry_time": entry_time,
        "exit_time": exit_time,
        "direction": direction,
        "entry_price": round(entry_price, 2),
        "exit_price": round(exit_price, 2),
        "qty": qty,
        "gross_pnl": round(gross_pnl, 2),
        "charges": round(charges, 2),
        "net_pnl": round(gross_pnl - charges, 2),
        "exit_reason": exit_reason,
        "capital_after": round(capital_after, 2),
    }


def _risk_sized_qty(
    sizing_capital: float,
    reference_price: float,
    max_risk_pct: float,
    sl_points: float,
    max_qty: int,
) -> int:
    risk_amount = sizing_capital * max_risk_pct / 100.0
    qty_by_risk = int(risk_amount / sl_points) if sl_points > 0 else max_qty
    qty_by_capital = int(sizing_capital / reference_price) if reference_price > 0 else 0
    if qty_by_capital < 1:
        return 0
    return max(1, min(qty_by_risk, qty_by_capital, max_qty))


def _indicator_snapshot(prepared: pd.DataFrame, i: int) -> dict[str, float]:
    skip = {"open", "high", "low", "close", "volume"}
    result: dict[str, float] = {}
    for col in prepared.columns:
        if col.lower() in skip:
            continue
        val = prepared.iat[i, prepared.columns.get_loc(col)]
        if isinstance(val, (int, float)) and not (val != val):  # not NaN
            result[col] = round(float(val), 4)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Core simulation
# ─────────────────────────────────────────────────────────────────────────────

def _run_all_day_by_day(
    strategy_instances: dict[str, Any],
    prepared_dfs: dict[str, pd.DataFrame],
    symbols_meta: dict[str, dict],
    capital: float,
    top_n: int,
    selected_dates_by_symbol: dict[str, set] | None,
    daily_picks: dict[date, list[dict]] | None,
    actual_start: date,
    sl_points: float,
    tp_points: float,
    max_qty: int,
    max_risk_pct: float,
    squareoff_hour: int,
    squareoff_min: int,
    daily_loss_limit: float,
    max_trades_per_day: int,
    max_consecutive_losses: int,
    tsl_enabled: bool,
    tsl_mode: str,
    tsl_value: float,
    tsl_activation_gap: float,
    journal: BacktestJournal | None,
    allocator=None,
) -> dict[str, list[dict]]:
    """
    Processes all symbols day-by-day in chronological order.
    Bars before actual_start are used for indicator warmup only — no trades.
    A shared pool funds all positions; daily risk limits are global.
    """
    pool = capital
    sym_capital: dict[str, float] = {sym: 0.0 for sym in strategy_instances}

    states: dict[str, dict] = {
        symbol: {
            "direction": "FLAT",
            "entry_price": 0.0,
            "entry_qty": 0,
            "entry_time": None,
            "sl_price": 0.0,
            "tp_price": 0.0,
            "tsl": None,
            "pending_entry": None,
            "pending_exit": False,
            "trades": [],
        }
        for symbol in strategy_instances
    }

    # Group bars by day: date -> sorted [(ts, symbol, bar_idx)]
    bars_by_day: dict[date, list] = {}
    for symbol, strategy in strategy_instances.items():
        warmup = max(strategy.required_history_bars() - 1, 1)
        prepared = prepared_dfs[symbol]
        for i in range(warmup, len(prepared)):
            ts = prepared.index[i]
            d = ts.astimezone(IST).date()
            bars_by_day.setdefault(d, []).append((ts, symbol, i))
    for d in bars_by_day:
        bars_by_day[d].sort(key=lambda x: x[0])

    # Shared daily state
    trades_today = 0
    day_halted = False
    consecutive_losses = 0
    day_start_pool = pool
    day_num = 0

    def close_trade(symbol: str, exit_price: float, exit_reason: str, bar_time) -> None:
        nonlocal pool, trades_today, consecutive_losses, day_halted

        state = states[symbol]
        meta = symbols_meta[symbol]

        if state["direction"] == "LONG":
            gross_pnl = (exit_price - state["entry_price"]) * state["entry_qty"]
            buy_price, sell_price = state["entry_price"], exit_price
        else:
            gross_pnl = (state["entry_price"] - exit_price) * state["entry_qty"]
            buy_price, sell_price = exit_price, state["entry_price"]

        ch = calculate_charges(
            segment=meta["segment"],
            buy_price=buy_price,
            sell_price=sell_price,
            quantity=state["entry_qty"],
            exchange=meta["exchange"],
        )
        net_pnl = gross_pnl - ch.total_charges
        pool += net_pnl
        sym_capital[symbol] += net_pnl

        state["trades"].append(
            _trade_record(
                state["entry_time"], bar_time, state["entry_price"], exit_price,
                state["entry_qty"], gross_pnl, ch.total_charges, exit_reason,
                sym_capital[symbol], state["direction"],
            )
        )

        if journal:
            journal.log_exit(
                bar_time, symbol, state["direction"], exit_price, state["entry_qty"],
                exit_reason, gross_pnl, ch.total_charges, net_pnl, pool,
            )

        state["direction"] = "FLAT"
        state["entry_price"] = 0.0
        state["entry_qty"] = 0
        state["entry_time"] = None
        state["sl_price"] = 0.0
        state["tp_price"] = 0.0
        state["pending_exit"] = False
        if state["tsl"] is not None:
            state["tsl"].reset()
            state["tsl"] = None

        trades_today += 1
        consecutive_losses = consecutive_losses + 1 if net_pnl < 0 else 0

        day_loss = day_start_pool - pool
        if daily_loss_limit > 0 and day_loss >= daily_loss_limit:
            day_halted = True
            _log.info("Daily loss limit hit (Rs%.2f) — halting entries", day_loss)
        if max_consecutive_losses > 0 and consecutive_losses >= max_consecutive_losses:
            day_halted = True
            _log.info("%d consecutive losses — halting entries", consecutive_losses)

    for trade_date in sorted(bars_by_day.keys()):
        is_trading_day = trade_date >= actual_start

        if selected_dates_by_symbol is not None:
            selected_today = {
                sym for sym in strategy_instances
                if trade_date in selected_dates_by_symbol.get(sym, set())
            }
        else:
            selected_today = set(strategy_instances.keys())

        if not selected_today:
            continue

        # Warmup days: process bars for indicator state but no trades/journal
        if not is_trading_day:
            for symbol in strategy_instances:
                states[symbol]["pending_entry"] = None
            continue

        day_num += 1
        trades_today = 0
        day_halted = False
        day_start_pool = pool
        n_active = len(selected_today)

        # Per-symbol capital allocation
        picks_today = daily_picks.get(trade_date, []) if daily_picks else []
        if picks_today and allocator is not None:
            alloc_map = allocator.allocate(pool, picks_today)
        else:
            per = pool / max(n_active, 1)
            alloc_map = {sym: per for sym in selected_today}

        for symbol in strategy_instances:
            if symbol not in selected_today:
                states[symbol]["pending_entry"] = None

        # Journal: day header + screener
        if journal:
            journal.log_day_header(trade_date, day_num, pool)
            if daily_picks and trade_date in daily_picks:
                journal.log_screener(daily_picks[trade_date], pool, alloc_map)
            else:
                journal.log_no_screener(sorted(selected_today), pool)

        # Track per-symbol P&L for this day (for day-end summary)
        sym_pnl_day_start = {sym: sym_capital[sym] for sym in selected_today}
        sym_trades_today: dict[str, int] = {sym: 0 for sym in selected_today}

        # Process bars chronologically
        for ts, symbol, bar_idx in bars_by_day[trade_date]:
            if symbol not in selected_today:
                continue

            state = states[symbol]
            prepared = prepared_dfs[symbol]
            row = prepared.iloc[bar_idx]

            ist_time = ts.astimezone(IST)
            past_sq = (ist_time.hour, ist_time.minute) >= (squareoff_hour, squareoff_min)

            bar_open = float(row["open"])
            bar_high = float(row["high"])
            bar_low = float(row["low"])
            bar_close = float(row["close"])
            atr_value = (
                float(prepared["atr"].iloc[bar_idx])
                if tsl_enabled and tsl_mode == "atr" and "atr" in prepared.columns
                else 0.0
            )

            # Pending exit: execute at bar open
            if state["direction"] != "FLAT" and state["pending_exit"]:
                close_trade(symbol, bar_open, "SIGNAL", ts)
                sym_trades_today[symbol] = sym_trades_today.get(symbol, 0) + 1

            # Pending entry: execute at bar open
            sym_alloc = alloc_map.get(symbol, pool / max(n_active, 1))
            if state["direction"] == "FLAT" and state["pending_entry"] and not past_sq:
                can_trade = (
                    sym_alloc > 0
                    and not day_halted
                    and not (max_trades_per_day > 0 and trades_today >= max_trades_per_day)
                )
                if can_trade:
                    qty = _risk_sized_qty(sym_alloc, bar_open, max_risk_pct, sl_points, max_qty)
                    if qty > 0:
                        state["direction"] = state["pending_entry"]
                        state["entry_price"] = bar_open
                        state["entry_qty"] = qty
                        state["entry_time"] = ts
                        if state["direction"] == "LONG":
                            state["sl_price"] = bar_open - sl_points
                            state["tp_price"] = bar_open + tp_points
                        else:
                            state["sl_price"] = bar_open + sl_points
                            state["tp_price"] = bar_open - tp_points
                        if tsl_enabled:
                            tsl_obj = TrailingSL(
                                mode=tsl_mode, value=tsl_value, activation_gap=tsl_activation_gap
                            )
                            tsl_obj.arm(
                                bar_open,
                                "long" if state["direction"] == "LONG" else "short",
                                atr=atr_value,
                            )
                            state["tsl"] = tsl_obj
                        if journal:
                            journal.log_entry(
                                ts, symbol, state["direction"], bar_open, qty,
                                state["sl_price"], state["tp_price"], sym_alloc,
                            )
                state["pending_entry"] = None

            # SL / TP / TSL / squareoff
            if state["direction"] == "LONG":
                if state["tsl"] is not None:
                    hit, tsl_exit = state["tsl"].simulate_bar(bar_high, bar_low)
                    if hit:
                        close_trade(symbol, tsl_exit, "TSL", ts)
                        sym_trades_today[symbol] = sym_trades_today.get(symbol, 0) + 1
                        continue
                if bar_low <= state["sl_price"]:
                    close_trade(symbol, state["sl_price"], "SL", ts)
                    sym_trades_today[symbol] = sym_trades_today.get(symbol, 0) + 1
                    continue
                if bar_high >= state["tp_price"]:
                    close_trade(symbol, state["tp_price"], "TP", ts)
                    sym_trades_today[symbol] = sym_trades_today.get(symbol, 0) + 1
                    continue
                if past_sq:
                    close_trade(symbol, bar_close, "SQUAREOFF", ts)
                    sym_trades_today[symbol] = sym_trades_today.get(symbol, 0) + 1
                    continue

            elif state["direction"] == "SHORT":
                if state["tsl"] is not None:
                    hit, tsl_exit = state["tsl"].simulate_bar(bar_high, bar_low)
                    if hit:
                        close_trade(symbol, tsl_exit, "TSL", ts)
                        sym_trades_today[symbol] = sym_trades_today.get(symbol, 0) + 1
                        continue
                if bar_high >= state["sl_price"]:
                    close_trade(symbol, state["sl_price"], "SL", ts)
                    sym_trades_today[symbol] = sym_trades_today.get(symbol, 0) + 1
                    continue
                if bar_low <= state["tp_price"]:
                    close_trade(symbol, state["tp_price"], "TP", ts)
                    sym_trades_today[symbol] = sym_trades_today.get(symbol, 0) + 1
                    continue
                if past_sq:
                    close_trade(symbol, bar_close, "SQUAREOFF", ts)
                    sym_trades_today[symbol] = sym_trades_today.get(symbol, 0) + 1
                    continue

            # Generate signal
            strategy = strategy_instances[symbol]
            signal = strategy.signal_from_prepared(prepared, bar_idx, state["direction"])

            if journal:
                inds = _indicator_snapshot(prepared, bar_idx)
                journal.log_bar(ts, symbol, bar_open, bar_high, bar_low, bar_close,
                                inds, signal, state["direction"])

            if state["direction"] == "LONG":
                state["pending_exit"] = signal == "SELL"
            elif state["direction"] == "SHORT":
                state["pending_exit"] = signal == "COVER"
            elif (
                not past_sq
                and not day_halted
                and not (max_trades_per_day > 0 and trades_today >= max_trades_per_day)
            ):
                if signal == "BUY":
                    state["pending_entry"] = "LONG"
                elif signal == "SHORT":
                    state["pending_entry"] = "SHORT"

        # End of day: close remaining positions
        last_by_sym: dict[str, tuple] = {}
        for ts, sym, bar_idx in bars_by_day[trade_date]:
            if sym in selected_today:
                last_by_sym[sym] = (ts, bar_idx)

        for symbol in selected_today:
            state = states[symbol]
            if state["direction"] != "FLAT" and symbol in last_by_sym:
                last_ts, last_idx = last_by_sym[symbol]
                close_trade(
                    symbol,
                    float(prepared_dfs[symbol].iloc[last_idx]["close"]),
                    "END_OF_DAY",
                    last_ts,
                )
                sym_trades_today[symbol] = sym_trades_today.get(symbol, 0) + 1
            state["pending_entry"] = None
            state["pending_exit"] = False

        # Journal: end-of-day summary
        if journal:
            sym_pnl = {sym: sym_capital[sym] - sym_pnl_day_start[sym] for sym in selected_today}
            journal.log_day_end(
                trade_date, pool, sorted(selected_today), sym_pnl, sym_trades_today
            )

    return {symbol: state["trades"] for symbol, state in states.items()}


# ─────────────────────────────────────────────────────────────────────────────
# Reports (console)
# ─────────────────────────────────────────────────────────────────────────────

def _print_report(trades: list[dict], capital: float, symbol: str, interval: str, strategy_name: str) -> None:
    if not trades:
        print(f"\n  {symbol}: No trades executed.")
        return

    net_pnls = [t["net_pnl"] for t in trades]
    gross_pnls = [t["gross_pnl"] for t in trades]
    charges = [t["charges"] for t in trades]
    winners = [p for p in net_pnls if p > 0]
    losers = [p for p in net_pnls if p < 0]
    total_net = sum(net_pnls)
    total_gross = sum(gross_pnls)
    total_charges = sum(charges)
    win_rate = len(winners) / len(trades) * 100
    avg_win = sum(winners) / len(winners) if winners else 0
    avg_loss = sum(losers) / len(losers) if losers else 0
    rr = abs(avg_win / avg_loss) if avg_loss else float("inf")

    equity = [capital]
    running = capital
    for p in net_pnls:
        running += p
        equity.append(running)
    peak = capital
    max_dd = 0.0
    max_dd_pct = 0.0
    for e in equity:
        if e > peak:
            peak = e
        dd = peak - e
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = dd / peak * 100 if peak else 0

    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1

    w = 55
    print("\n" + "=" * w)
    print(f"  BACKTEST REPORT — {symbol}  [{interval}]")
    print("=" * w)
    print(f"  Strategy           : {strategy_name}")
    print(f"  Opening capital    : Rs{capital:>12,.2f}")
    print(f"  Final capital      : Rs{capital + total_net:>12,.2f}")
    print(f"  Total return       : {total_net / capital * 100:>+10.2f}%")
    print(f"  Total net P&L      : Rs{total_net:>+12,.2f}")
    print(f"  Total gross P&L    : Rs{total_gross:>+12,.2f}")
    print(f"  Total charges paid : Rs{total_charges:>12,.2f}")
    print("-" * w)
    long_t = [t for t in trades if t["direction"] == "LONG"]
    short_t = [t for t in trades if t["direction"] == "SHORT"]
    print(f"  Total trades       : {len(trades):>6}  (L:{len(long_t)}  S:{len(short_t)})")
    print(f"  Winners            : {len(winners):>6}  ({win_rate:.1f}%)")
    print(f"  Losers             : {len(losers):>6}  ({100 - win_rate:.1f}%)")
    print(f"  Avg win            : Rs{avg_win:>+10,.2f}")
    print(f"  Avg loss           : Rs{avg_loss:>+10,.2f}")
    print(f"  Reward / Risk      : {rr:>10.2f}x")
    print(f"  Max drawdown       : Rs{max_dd:>10,.2f}  ({max_dd_pct:.1f}%)")
    print("-" * w)
    print("  Exit reasons:")
    for reason, cnt in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"    {reason:<20} : {cnt}")
    print("=" * w)

    w2 = 80
    print("\n  PER-TRADE DETAIL")
    print("-" * w2)
    print(
        f"  {'#':>3}  {'Entry':^17}  {'D'}  "
        f"{'Entry':>8}  {'Exit':>8}  {'Qty':>4}  "
        f"{'Net P&L':>10}  {'Capital':>11}  Reason"
    )
    print("-" * w2)
    for idx, t in enumerate(trades, 1):
        ts_str = t["entry_time"].astimezone(IST).strftime("%d-%b %H:%M")
        sign = "+" if t["net_pnl"] >= 0 else ""
        side = "L" if t["direction"] == "LONG" else "S"
        print(
            f"  {idx:>3}  {ts_str:<17}  {side}  "
            f"Rs{t['entry_price']:>7.2f}  Rs{t['exit_price']:>7.2f}  "
            f"{t['qty']:>4}  "
            f"{sign}Rs{t['net_pnl']:>8,.2f}  "
            f"Rs{t['capital_after']:>9,.2f}  "
            f"{t['exit_reason']}"
        )
    print("=" * w2)


def _print_aggregate_report(
    results: list[dict],
    total_capital: float,
    final_pool: float,
    strategy_name: str,
    screener_mode: bool,
) -> None:
    all_trades = [t for r in results for t in r["trades"]]
    if not all_trades:
        print(f"\nAggregate ({len(results)} symbols): no trades executed.")
        return

    total_net = final_pool - total_capital
    total_gross = sum(t["gross_pnl"] for t in all_trades)
    total_charges = sum(t["charges"] for t in all_trades)
    winners = [t for t in all_trades if t["net_pnl"] > 0]
    win_rate = len(winners) / len(all_trades) * 100
    sign = "+" if total_net >= 0 else ""

    w = 60
    print("\n" + "=" * w)
    print(f"  AGGREGATE SUMMARY — {len(results)} SYMBOLS")
    print("=" * w)
    print(f"  Strategy           : {strategy_name}")
    print(f"  Selection          : {'walk-forward screener' if screener_mode else 'no screener'}")
    print(f"  Opening capital    : Rs{total_capital:>12,.2f}")
    print(f"  Final capital      : Rs{final_pool:>12,.2f}")
    print(f"  Total return       : {sign}{total_net / total_capital * 100:>+.2f}%")
    print(f"  Total net P&L      : {sign}Rs{total_net:>+,.2f}")
    print(f"  Total gross P&L    : Rs{total_gross:>+,.2f}")
    print(f"  Total charges      : Rs{total_charges:>,.2f}")
    print(f"  Total trades       : {len(all_trades)}")
    print(f"  Win rate           : {win_rate:.1f}%")
    print("-" * w)
    print("  Per-symbol (sorted by net P&L):")
    for r in sorted(results, key=lambda x: sum(t["net_pnl"] for t in x["trades"]), reverse=True):
        sp = sum(t["net_pnl"] for t in r["trades"])
        st = len(r["trades"])
        sw = sum(1 for t in r["trades"] if t["net_pnl"] > 0)
        swr = sw / st * 100 if st else 0
        s2 = "+" if sp >= 0 else ""
        print(f"    {r['symbol']:<16}  {st:>4} trades  WR={swr:.0f}%  {s2}Rs{sp:>+,.2f}")
    print("=" * w)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    args = _parse_args()

    try:
        cfg = _load_config(args.config)
    except FileNotFoundError:
        print(f"Config file not found: {args.config}", file=sys.stderr)
        sys.exit(1)

    strat_template = _base_strategy_template(cfg)
    strategy_name = args.strategy or str(cfg.get("strategy", {}).get("name", "ema_crossover"))
    if strategy_name not in STRATEGIES:
        print(f"Unknown strategy: {strategy_name!r}. Available: {sorted(STRATEGIES)}", file=sys.stderr)
        sys.exit(1)
    if args.interval:
        strat_template["interval"] = args.interval

    risk_cfg = cfg["risk"]
    broker_cfg = cfg["broker"]
    screener_cfg = cfg.get("screener", {})
    screener_enabled = bool(screener_cfg.get("enabled", False))
    default_exchange = str(strat_template.get("exchange", "NSE")).upper()
    interval = strat_template.get("interval", "FIVE_MINUTE")

    # Determine symbols
    if args.symbols:
        symbols_list = [
            {"symbol": s.strip().upper(), "exchange": default_exchange}
            for s in args.symbols.split(",") if s.strip()
        ]
    elif screener_enabled:
        symbols_list = _load_watchlist_symbols(screener_cfg, default_exchange)
    else:
        symbols_list = [{"symbol": str(strat_template["symbol"]).upper(), "exchange": default_exchange}]

    if not symbols_list:
        symbols_list = [{"symbol": str(strat_template["symbol"]).upper(), "exchange": default_exchange}]

    multi = len(symbols_list) > 1

    # Risk / sizing parameters
    capital = args.capital or float(risk_cfg["capital"])
    top_n = int(screener_cfg.get("top_n", 5)) if screener_enabled and multi else len(symbols_list)
    per_sym_capital = capital / max(top_n, 1)

    sl_points = float(risk_cfg["sl_points"])
    tp_points = float(risk_cfg["tp_points"])
    max_qty = int(risk_cfg["max_qty"])
    max_risk_pct = float(risk_cfg["max_risk_pct"])
    daily_loss_limit = float(risk_cfg.get("daily_loss_limit", 0))
    max_trades_per_day = int(risk_cfg.get("max_trades_per_day", 0))
    max_consecutive_losses = int(risk_cfg.get("max_consecutive_losses", 0))

    tsl_cfg = risk_cfg.get("trailing_sl", {})
    tsl_enabled = bool(tsl_cfg.get("enabled", False)) and not args.no_tsl
    tsl_mode = str(tsl_cfg.get("mode", "points"))
    tsl_value = float(tsl_cfg.get("value", 5.0))
    tsl_activation_gap = float(tsl_cfg.get("activation_gap", 0.0))
    tsl_atr_period = int(tsl_cfg.get("atr_period", 14))

    squareoff_time = broker_cfg.get("squareoff_time", "15:15")
    sq_h, sq_m = map(int, squareoff_time.split(":"))

    tsl_desc = f"TSL={tsl_mode}:{tsl_value} gap=Rs{tsl_activation_gap}" if tsl_enabled else "TSL=off"
    risk_limits = []
    if daily_loss_limit > 0:
        risk_limits.append(f"DayLoss=Rs{daily_loss_limit:.0f}")
    if max_trades_per_day > 0:
        risk_limits.append(f"MaxTrades/Day={max_trades_per_day}")
    if max_consecutive_losses > 0:
        risk_limits.append(f"MaxConsecLoss={max_consecutive_losses}")
    risk_desc = "  ".join(risk_limits) if risk_limits else "no daily limits"

    # ── Compute warmup pre-fetch ──────────────────────────────────────────────
    # Create a sample strategy instance solely to query required_history_bars().
    sample_sym = symbols_list[0]["symbol"]
    _sample_cfg = copy.deepcopy(cfg)
    _sample_cfg["strategy"] = {**strat_template, "symbol": sample_sym, "name": strategy_name}
    _sample_strat = STRATEGIES[strategy_name](_sample_cfg)
    warmup_bars = _sample_strat.required_history_bars()
    warmup_days = _warmup_extra_days(interval, warmup_bars)
    del _sample_strat

    actual_start = datetime.strptime(args.from_date, "%Y-%m-%d").date()
    fetch_from = datetime.strptime(args.from_date, "%Y-%m-%d") - timedelta(days=warmup_days)
    from_date = fetch_from.strftime("%Y-%m-%d") + " 09:15"
    to_date = args.to_date + " 15:30"

    sym_desc = (
        f"{len(symbols_list)} symbols (screener)" if screener_enabled and multi
        else (",".join(s["symbol"] for s in symbols_list) if multi else symbols_list[0]["symbol"])
    )
    print(f"\nBacktest: {sym_desc} | {interval} | {strategy_name}")
    print(f"Period  : {args.from_date} -> {args.to_date}  (fetching {warmup_days} extra days warmup before start)")
    print(f"Capital : Rs{capital:,.0f}" + (f"  (~Rs{per_sym_capital:,.0f}/symbol if {top_n} active)" if multi else ""))
    print(f"Risk    : SL=Rs{sl_points}  TP=Rs{tp_points}  MaxQty={max_qty}  {tsl_desc}")
    print(f"Limits  : {risk_desc}")

    # Login
    try:
        session = AngelSession.from_env()
        session.login()
    except Exception as exc:
        print(f"\nLogin failed: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        print("\nLoading instrument master...", end=" ", flush=True)
        master = InstrumentMaster()
        master.load()
        print("done")

        valid_symbols = []
        for sym_info in symbols_list:
            token = master.get_token(sym_info["exchange"], sym_info["symbol"])
            if token:
                valid_symbols.append({**sym_info, "token": token})
            else:
                print(f"  Warning: {sym_info['symbol']} not found on {sym_info['exchange']} — skipping")

        if not valid_symbols:
            print("No valid symbols found.", file=sys.stderr)
            sys.exit(1)

        # Walk-forward screener gate
        selected_dates_by_symbol: dict[str, set] = {}
        daily_picks: dict[date, list[dict]] = {}
        if screener_enabled and multi:
            lookback = int(screener_cfg.get("lookback_days", 45))
            from_daily_ext = (
                datetime.strptime(args.from_date, "%Y-%m-%d") - timedelta(days=lookback + 5)
            ).strftime("%Y-%m-%d 09:00")
            to_daily = args.to_date + " 15:30"

            print(f"\nFetching daily candles for {len(valid_symbols)} symbols (screener look-back)...")
            daily_dfs: dict[str, pd.DataFrame] = {}
            for sym_info in valid_symbols:
                time.sleep(0.35)
                try:
                    daily_df = _fetch_all_candles(
                        session, sym_info["exchange"], sym_info["token"],
                        "ONE_DAY", from_daily_ext, to_daily,
                    )
                    daily_dfs[sym_info["symbol"]] = daily_df
                except Exception as exc:
                    _log.warning("No daily data for %s: %s", sym_info["symbol"], exc)

            if daily_dfs:
                print("Computing walk-forward screener selection per day...")
                screener_instance = get_screener(cfg)
                selected_dates_by_symbol, daily_picks = _compute_screener_selection_per_day(
                    daily_dfs=daily_dfs,
                    screener_cfg=screener_cfg,
                    screener=screener_instance,
                    backtest_start=actual_start,
                    backtest_end=datetime.strptime(args.to_date, "%Y-%m-%d").date(),
                )

        # Fetch all intraday candles (includes warmup period before actual_start)
        print(f"\nFetching intraday candles ({interval}) for {len(valid_symbols)} symbol(s)...")
        print(f"  (includes {warmup_days} warmup days before {args.from_date})")
        intraday_dfs: dict[str, pd.DataFrame] = {}
        for sym_info in valid_symbols:
            time.sleep(0.35)
            symbol = sym_info["symbol"]
            try:
                df = _fetch_all_candles(
                    session, sym_info["exchange"], sym_info["token"], interval, from_date, to_date
                )
                intraday_dfs[symbol] = df
                print(f"  {symbol}: {len(df)} bars total ({warmup_days}d warmup + requested range)")
            except Exception as exc:
                print(f"  {symbol}: fetch failed — {exc}")

        if not intraday_dfs:
            print("No candle data available.", file=sys.stderr)
            sys.exit(1)

        # Build strategy instances and prepared dataframes
        print("Preparing strategy signals...")
        strategy_instances: dict[str, Any] = {}
        prepared_dfs: dict[str, pd.DataFrame] = {}
        symbols_meta: dict[str, dict] = {}

        active_syms = [s for s in valid_symbols if s["symbol"] in intraday_dfs]
        for sym_info in active_syms:
            symbol = sym_info["symbol"]
            strat_cfg = copy.deepcopy(strat_template)
            strat_cfg.update({"symbol": symbol, "exchange": sym_info["exchange"], "name": strategy_name})
            merged_cfg = copy.deepcopy(cfg)
            merged_cfg["strategy"] = strat_cfg

            strategy = STRATEGIES[strategy_name](merged_cfg)
            strategy_instances[symbol] = strategy

            prepared = strategy.prepare_dataframe(intraday_dfs[symbol].copy())
            if tsl_enabled and tsl_mode == "atr" and "atr" not in prepared.columns:
                prepared["atr"] = compute_atr(
                    prepared["high"], prepared["low"], prepared["close"], tsl_atr_period
                )
            prepared_dfs[symbol] = prepared
            symbols_meta[symbol] = {
                "exchange": sym_info["exchange"],
                "segment": _resolve_trade_segment(strat_cfg, broker_cfg),
            }

        if not strategy_instances:
            print("No strategies could be prepared.", file=sys.stderr)
            sys.exit(1)

        # Set up journal
        journal_dir = Path("data/journal")
        journal_dir.mkdir(parents=True, exist_ok=True)
        journal_path = journal_dir / f"backtest_{strategy_name}_{args.from_date}_{args.to_date}.txt"
        journal = BacktestJournal(journal_path)
        journal.log_header(
            strategy=strategy_name,
            from_date=args.from_date,
            to_date=args.to_date,
            capital=capital,
            interval=interval,
            top_n=top_n,
            sl=sl_points,
            tp=tp_points,
            tsl_desc=tsl_desc,
            warmup_bars=warmup_bars,
            warmup_days=warmup_days,
        )

        # Day-by-day simulation
        mode_label = "walk-forward screener" if screener_enabled and multi else "all days"
        print(f"\nRunning day-by-day simulation ({mode_label}, {len(strategy_instances)} symbol(s))...")
        print(f"Journal: {journal_path}\n")

        allocator = get_allocator(cfg)
        all_symbol_trades = _run_all_day_by_day(
            strategy_instances=strategy_instances,
            prepared_dfs=prepared_dfs,
            symbols_meta=symbols_meta,
            capital=capital,
            top_n=top_n,
            selected_dates_by_symbol=selected_dates_by_symbol if screener_enabled and multi else None,
            daily_picks=daily_picks if screener_enabled and multi else None,
            actual_start=actual_start,
            sl_points=sl_points,
            tp_points=tp_points,
            max_qty=max_qty,
            max_risk_pct=max_risk_pct,
            squareoff_hour=sq_h,
            squareoff_min=sq_m,
            daily_loss_limit=daily_loss_limit,
            max_trades_per_day=max_trades_per_day,
            max_consecutive_losses=max_consecutive_losses,
            tsl_enabled=tsl_enabled,
            tsl_mode=tsl_mode,
            tsl_value=tsl_value,
            tsl_activation_gap=tsl_activation_gap,
            journal=journal,
            allocator=allocator,
        )

        # Collect results
        all_results: list[dict] = []
        final_pool = capital + sum(
            t["net_pnl"]
            for trades in all_symbol_trades.values()
            for t in trades
        )
        for sym_info in active_syms:
            symbol = sym_info["symbol"]
            trades = all_symbol_trades.get(symbol, [])
            all_results.append({
                "symbol": symbol,
                "exchange": sym_info["exchange"],
                "trades": trades,
                "capital": per_sym_capital,
                "segment": symbols_meta[symbol]["segment"],
            })

        # Print reports
        if not multi:
            r = all_results[0]
            _print_report(r["trades"], capital, r["symbol"], interval, strategy_name)
        else:
            _print_aggregate_report(
                all_results,
                total_capital=capital,
                final_pool=final_pool,
                strategy_name=strategy_name,
                screener_mode=screener_enabled,
            )

        # Write aggregate to journal
        journal.log_aggregate(all_results, capital, final_pool, strategy_name)
        journal.close()
        print(f"\nFull journal saved to: {journal_path}")

    finally:
        try:
            session.logout()
        except Exception:
            pass


if __name__ == "__main__":
    main()
