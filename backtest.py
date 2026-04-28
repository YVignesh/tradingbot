"""
backtest.py — Strategy Backtester
===================================
Fetches historical OHLCV candles via AngelOne API and replays them
through a chosen strategy, producing a detailed trade report.

Usage:
    python backtest.py --strategy ema_crossover \
                       --from 2026-01-01 --to 2026-04-01 \
                       --capital 50000

Options:
    --strategy   Strategy name (must match a key in STRATEGIES dict)
    --from       Start date  YYYY-MM-DD  (market open 09:15 IST assumed)
    --to         End date    YYYY-MM-DD  (market close 15:30 IST assumed)
    --capital    Opening capital in ₹   (default: from config.json)
    --config     Path to config.json    (default: config.json)
    --interval   Override candle interval (e.g. FIVE_MINUTE, ONE_HOUR)

Environment:
    Credentials loaded from .env via python-dotenv (same as main.py).
    ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_MPIN, ANGEL_TOTP_SECRET must be set.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

import pandas as pd

from broker.session import AngelSession
from broker.market_data import get_candles, candles_to_dataframe
from broker.instruments import InstrumentMaster
from broker.charges import calculate_charges, Segment
from indicators.trend import ema
from indicators.volatility import atr as compute_atr
from risk.trailing_sl import TrailingSL
from utils import get_logger

_log = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

STRATEGIES = ["ema_crossover"]


# ── Config / CLI ──────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="AngelOne strategy backtester")
    p.add_argument("--strategy", default="ema_crossover",
                   choices=STRATEGIES, help="Strategy to backtest")
    p.add_argument("--from",    dest="from_date", required=True,
                   help="Start date YYYY-MM-DD")
    p.add_argument("--to",      dest="to_date",   required=True,
                   help="End date   YYYY-MM-DD")
    p.add_argument("--capital", type=float, default=None,
                   help="Opening capital ₹ (overrides config.json)")
    p.add_argument("--config",  default="config.json",
                   help="Path to config.json")
    p.add_argument("--interval", default=None,
                   help="Override candle interval (e.g. FIVE_MINUTE)")
    p.add_argument("--no-tsl", action="store_true",
                   help="Disable trailing stop loss even if enabled in config.json")
    return p.parse_args()


def _load_config(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


# ── Data fetching ─────────────────────────────────────────────────────────────

def _fetch_all_candles(
    session,
    exchange: str,
    token: str,
    interval: str,
    from_date: str,
    to_date: str,
) -> pd.DataFrame:
    """
    Fetch candles in 60-day chunks (AngelOne API limit for intraday intervals)
    and concatenate into a single DataFrame sorted by time.
    """
    fmt = "%Y-%m-%d %H:%M"
    start = datetime.strptime(from_date, fmt).replace(tzinfo=IST)
    end   = datetime.strptime(to_date,   fmt).replace(tzinfo=IST)

    chunk_days = 60
    all_candles: list[dict] = []
    cursor = start

    while cursor < end:
        chunk_end = min(cursor + timedelta(days=chunk_days), end)
        from_str = cursor.strftime(fmt)
        to_str   = chunk_end.strftime(fmt)
        _log.info("Fetching candles %s → %s", from_str, to_str)
        try:
            batch = get_candles(session, exchange, token, interval, from_str, to_str)
            all_candles.extend(batch)
        except Exception as e:
            _log.warning("Candle fetch failed for chunk %s→%s: %s", from_str, to_str, e)
        cursor = chunk_end + timedelta(minutes=1)

    if not all_candles:
        raise RuntimeError("No candles returned for the requested date range.")

    df = candles_to_dataframe(all_candles)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    _log.info("Total candles fetched: %d", len(df))
    return df


# ── EMA Crossover signal replay ───────────────────────────────────────────────

def _run_ema_crossover(
    df: pd.DataFrame,
    fast: int,
    slow: int,
    capital: float,
    sl_points: float,
    tp_points: float,
    max_qty: int,
    max_risk_pct: float,
    squareoff_hour: int,
    squareoff_min: int,
    exchange: str,
    daily_loss_limit: float = 0.0,
    max_trades_per_day: int = 0,
    max_consecutive_losses: int = 0,
    tsl_enabled: bool = False,
    tsl_mode: str = "points",
    tsl_value: float = 5.0,
    tsl_activation_gap: float = 0.0,
    tsl_atr_period: int = 14,
) -> list[dict]:
    """
    Replay EMA crossover signals bar-by-bar on df.

    Returns a list of trade dicts (one per completed round-trip):
        entry_time, exit_time, entry_price, exit_price,
        qty, gross_pnl, charges, net_pnl, exit_reason

    Risk limits enforced (0 = unlimited):
        daily_loss_limit        — halt new entries when day loss >= this ₹ amount
        max_trades_per_day      — halt new entries after this many trades in a day
        max_consecutive_losses  — halt new entries after this many back-to-back losses
    """
    df = df.copy()
    df["ema_fast"] = ema(df["close"], fast)
    df["ema_slow"] = ema(df["close"], slow)

    if tsl_enabled and tsl_mode == "atr":
        df["atr"] = compute_atr(df["high"], df["low"], df["close"], tsl_atr_period)

    trades: list[dict] = []

    direction       = "FLAT"   # "LONG" | "SHORT" | "FLAT"
    entry_price     = 0.0
    entry_qty       = 0
    entry_time      = None

    sl_price        = 0.0
    tp_price        = 0.0
    current_capital = capital
    tsl: TrailingSL | None = None

    # Daily risk tracking
    current_day         = None   # IST date of the current bar
    day_start_capital   = capital
    trades_today        = 0
    consecutive_losses  = 0
    day_halted          = False

    def _close_trade(exit_p, exit_reason, bar_time):
        nonlocal direction, current_capital, tsl
        nonlocal trades_today, consecutive_losses, day_halted
        if direction == "LONG":
            gross_pnl = (exit_p - entry_price) * entry_qty
            buy_p, sell_p = entry_price, exit_p
        else:  # SHORT
            gross_pnl = (entry_price - exit_p) * entry_qty
            buy_p, sell_p = exit_p, entry_price
        chg = calculate_charges(
            segment=Segment.EQUITY_INTRADAY,
            buy_price=buy_p, sell_price=sell_p,
            quantity=entry_qty, exchange=exchange,
        )
        net_pnl = gross_pnl - chg.total_charges
        current_capital += net_pnl
        trades.append(_trade_record(
            entry_time, bar_time, entry_price, exit_p,
            entry_qty, gross_pnl, chg.total_charges, exit_reason,
            current_capital, direction,
        ))
        direction = "FLAT"
        if tsl is not None:
            tsl.reset()
            tsl = None

        # Update daily risk counters after the trade closes
        trades_today += 1
        if net_pnl < 0:
            consecutive_losses += 1
        else:
            consecutive_losses = 0

        day_loss = day_start_capital - current_capital
        if daily_loss_limit > 0 and day_loss >= daily_loss_limit:
            day_halted = True
            _log.info("Daily loss limit reached (₹%.2f) — no more entries today", day_loss)
        if max_consecutive_losses > 0 and consecutive_losses >= max_consecutive_losses:
            day_halted = True
            _log.info("%d consecutive losses — no more entries today", consecutive_losses)

    # We need slow+1 warm-up bars before signals are valid
    warmup = slow + 1

    for i in range(warmup, len(df)):
        row  = df.iloc[i]
        bar_time = df.index[i]

        # IST hour/minute for squareoff check
        ist_time = bar_time.astimezone(IST)
        past_sq  = (ist_time.hour, ist_time.minute) >= (squareoff_hour, squareoff_min)

        # Day-change: reset daily counters at the first bar of each new IST date
        bar_date = ist_time.date()
        if bar_date != current_day:
            current_day       = bar_date
            day_start_capital = current_capital
            trades_today      = 0
            consecutive_losses = 0
            day_halted        = False

        high  = row["high"]
        low   = row["low"]
        close = row["close"]

        ema_bullish = df["ema_fast"].iloc[i] > df["ema_slow"].iloc[i]

        if direction == "LONG":
            # TSL check — runs before static SL
            if tsl is not None:
                hit, tsl_exit = tsl.simulate_bar(high, low)
                if hit:
                    _close_trade(tsl_exit, "TSL", bar_time)
                    continue

            # Static SL hard floor (catches gap-downs before TSL activates)
            if low <= sl_price:
                _close_trade(sl_price, "SL", bar_time)
                continue

            if high >= tp_price:
                _close_trade(tp_price, "TP", bar_time)
                continue

            if past_sq:
                _close_trade(close, "SQUAREOFF", bar_time)
                continue

            # EMA turned bearish → exit long
            if not ema_bullish:
                _close_trade(close, "SIGNAL", bar_time)

        elif direction == "SHORT":
            # TSL check (for shorts: SL trails above, hits when price rises)
            if tsl is not None:
                hit, tsl_exit = tsl.simulate_bar(high, low)
                if hit:
                    _close_trade(tsl_exit, "TSL", bar_time)
                    continue

            # Static SL hard ceiling for shorts (price went UP through SL)
            if high >= sl_price:
                _close_trade(sl_price, "SL", bar_time)
                continue

            # TP for shorts: price went DOWN to target
            if low <= tp_price:
                _close_trade(tp_price, "TP", bar_time)
                continue

            if past_sq:
                _close_trade(close, "SQUAREOFF", bar_time)
                continue

            # EMA turned bullish → cover short
            if ema_bullish:
                _close_trade(close, "SIGNAL", bar_time)

        else:  # FLAT — look for entry
            if past_sq:
                continue

            # ── Risk guards: skip entry if any limit is breached ─────────────
            if current_capital <= 0:
                continue  # out of capital
            if day_halted:
                continue  # daily loss or consecutive-loss limit hit
            if max_trades_per_day > 0 and trades_today >= max_trades_per_day:
                continue  # reached daily trade cap

            risk_amount    = current_capital * max_risk_pct / 100.0
            qty_by_risk    = int(risk_amount / sl_points)
            qty_by_capital = int(current_capital / close)
            if qty_by_capital < 1:
                continue  # cannot afford even one share at current capital
            qty = max(1, min(qty_by_risk, qty_by_capital, max_qty))
            atr_val = float(df["atr"].iloc[i]) if tsl_enabled and tsl_mode == "atr" else 0.0

            if ema_bullish:
                direction   = "LONG"
                entry_price = close
                entry_qty   = qty
                entry_time  = bar_time
                sl_price    = entry_price - sl_points
                tp_price    = entry_price + tp_points
                if tsl_enabled:
                    tsl = TrailingSL(mode=tsl_mode, value=tsl_value,
                                     activation_gap=tsl_activation_gap)
                    tsl.arm(entry_price, direction="long", atr=atr_val)

            elif not ema_bullish:
                direction   = "SHORT"
                entry_price = close
                entry_qty   = qty
                entry_time  = bar_time
                sl_price    = entry_price + sl_points   # SL is ABOVE entry for shorts
                tp_price    = entry_price - tp_points   # TP is BELOW entry for shorts
                if tsl_enabled:
                    tsl = TrailingSL(mode=tsl_mode, value=tsl_value,
                                     activation_gap=tsl_activation_gap)
                    tsl.arm(entry_price, direction="short", atr=atr_val)

    if direction != "FLAT":
        _close_trade(df.iloc[-1]["close"], "END_OF_DATA", df.index[-1])

    return trades


def _trade_record(
    entry_time, exit_time,
    entry_price: float, exit_price: float,
    qty: int,
    gross_pnl: float, charges: float,
    exit_reason: str,
    capital_after: float,
    direction: str = "LONG",
) -> dict:
    return {
        "entry_time":    entry_time,
        "exit_time":     exit_time,
        "direction":     direction,
        "entry_price":   round(entry_price,   2),
        "exit_price":    round(exit_price,    2),
        "qty":           qty,
        "gross_pnl":     round(gross_pnl,     2),
        "charges":       round(charges,       2),
        "net_pnl":       round(gross_pnl - charges, 2),
        "exit_reason":   exit_reason,
        "capital_after": round(capital_after, 2),
    }


# ── Report generation ─────────────────────────────────────────────────────────

def _print_report(trades: list[dict], capital: float, symbol: str, interval: str) -> None:
    if not trades:
        print("\nNo trades executed in the backtest period.")
        return

    net_pnls   = [t["net_pnl"]   for t in trades]
    gross_pnls = [t["gross_pnl"] for t in trades]
    charges    = [t["charges"]   for t in trades]

    winners    = [p for p in net_pnls if p > 0]
    losers     = [p for p in net_pnls if p < 0]
    total_net  = sum(net_pnls)
    total_gross= sum(gross_pnls)
    total_chg  = sum(charges)

    win_rate   = len(winners) / len(trades) * 100 if trades else 0
    avg_win    = sum(winners) / len(winners) if winners else 0
    avg_loss   = sum(losers)  / len(losers)  if losers  else 0
    rr_ratio   = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")

    # Drawdown calculation
    equity_curve = [capital]
    running = capital
    for p in net_pnls:
        running += p
        equity_curve.append(running)

    peak      = capital
    max_dd    = 0.0
    max_dd_pct= 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > max_dd:
            max_dd     = dd
            max_dd_pct = dd / peak * 100 if peak > 0 else 0

    final_capital = capital + total_net
    total_return  = total_net / capital * 100

    # Long / short breakdown
    long_trades  = [t for t in trades if t["direction"] == "LONG"]
    short_trades = [t for t in trades if t["direction"] == "SHORT"]

    # Exit reason breakdown
    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1

    w = 55
    print("\n" + "═" * w)
    print(f"  BACKTEST REPORT — {symbol}  [{interval}]")
    print("═" * w)
    print(f"  Opening capital    : ₹{capital:>12,.2f}")
    print(f"  Final capital      : ₹{final_capital:>12,.2f}")
    print(f"  Total return       : {total_return:>+10.2f}%")
    print(f"  Total net P&L      : ₹{total_net:>+12,.2f}")
    print(f"  Total gross P&L    : ₹{total_gross:>+12,.2f}")
    print(f"  Total charges paid : ₹{total_chg:>12,.2f}")
    print("─" * w)
    print(f"  Total trades       : {len(trades):>6}  (L:{len(long_trades)}  S:{len(short_trades)})")
    print(f"  Winners            : {len(winners):>6}  ({win_rate:.1f}%)")
    print(f"  Losers             : {len(losers):>6}  ({100 - win_rate:.1f}%)")
    print(f"  Average win        : ₹{avg_win:>+10,.2f}")
    print(f"  Average loss       : ₹{avg_loss:>+10,.2f}")
    print(f"  Reward / Risk      : {rr_ratio:>10.2f}x")
    print(f"  Max drawdown       : ₹{max_dd:>10,.2f}  ({max_dd_pct:.1f}%)")
    print("─" * w)
    print(f"  Exit reasons:")
    for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"    {reason:<20} : {count}")
    print("═" * w)

    # Per-trade table
    w2 = 80
    print("\n  PER-TRADE DETAIL")
    print("─" * w2)
    hdr = (f"  {'#':>3}  {'Entry time':<17}  {'D':>1}  "
           f"{'Entry':>8}  {'Exit':>8}  {'Qty':>4}  "
           f"{'Net P&L':>10}  {'Capital':>11}  {'Reason'}")
    print(hdr)
    print("─" * w2)
    for i, t in enumerate(trades, 1):
        ts   = t["entry_time"].astimezone(IST).strftime("%d-%b %H:%M")
        sign = "+" if t["net_pnl"] >= 0 else ""
        d    = "L" if t["direction"] == "LONG" else "S"
        print(
            f"  {i:>3}  {ts:<17}  {d}  "
            f"₹{t['entry_price']:>7.2f}  ₹{t['exit_price']:>7.2f}  "
            f"{t['qty']:>4}  "
            f"{sign}₹{t['net_pnl']:>8,.2f}  "
            f"₹{t['capital_after']:>9,.2f}  "
            f"{t['exit_reason']}"
        )
    print("═" * w2)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    args = _parse_args()

    try:
        cfg = _load_config(args.config)
    except FileNotFoundError:
        print(f"Config file not found: {args.config}", file=sys.stderr)
        sys.exit(1)

    strat_cfg = cfg["strategy"]
    risk_cfg  = cfg["risk"]
    broker_cfg= cfg["broker"]

    symbol   = strat_cfg["symbol"]
    exchange = strat_cfg["exchange"]
    interval = args.interval or strat_cfg.get("interval", "FIVE_MINUTE")
    fast     = int(strat_cfg.get("ema_fast", 9))
    slow     = int(strat_cfg.get("ema_slow", 21))

    capital                = args.capital or float(risk_cfg["capital"])
    sl_points              = float(risk_cfg["sl_points"])
    tp_points              = float(risk_cfg["tp_points"])
    max_qty                = int(risk_cfg["max_qty"])
    max_risk_pct           = float(risk_cfg["max_risk_pct"])
    daily_loss_limit       = float(risk_cfg.get("daily_loss_limit", 0))
    max_trades_per_day     = int(risk_cfg.get("max_trades_per_day", 0))
    max_consecutive_losses = int(risk_cfg.get("max_consecutive_losses", 0))

    tsl_cfg            = risk_cfg.get("trailing_sl", {})
    tsl_enabled        = bool(tsl_cfg.get("enabled", False)) and not args.no_tsl
    tsl_mode           = str(tsl_cfg.get("mode", "points"))
    tsl_value          = float(tsl_cfg.get("value", 5.0))
    tsl_activation_gap = float(tsl_cfg.get("activation_gap", 0.0))
    tsl_atr_period     = int(tsl_cfg.get("atr_period", 14))

    sq_time = broker_cfg.get("squareoff_time", "15:15")
    sq_h, sq_m = map(int, sq_time.split(":"))

    from_date = args.from_date + " 09:15"
    to_date   = args.to_date   + " 15:30"

    tsl_desc = (
        f"TSL={tsl_mode}:{tsl_value}  gap=₹{tsl_activation_gap}"
        if tsl_enabled else "TSL=off"
    )
    risk_limits = []
    if daily_loss_limit > 0:
        risk_limits.append(f"DayLoss=₹{daily_loss_limit:.0f}")
    if max_trades_per_day > 0:
        risk_limits.append(f"MaxTrades/Day={max_trades_per_day}")
    if max_consecutive_losses > 0:
        risk_limits.append(f"MaxConsecLoss={max_consecutive_losses}")
    risk_desc = "  ".join(risk_limits) if risk_limits else "no daily limits"

    print(f"\nBacktest: {symbol} | {interval} | EMA{fast}/{slow}")
    print(f"Period  : {args.from_date} → {args.to_date}")
    print(f"Capital : ₹{capital:,.0f}  |  SL=₹{sl_points}  TP=₹{tp_points}  MaxQty={max_qty}  {tsl_desc}")
    print(f"Limits  : {risk_desc}")

    # Login
    try:
        session = AngelSession.from_env()
        session.login()
    except Exception as e:
        print(f"\nLogin failed: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        # Resolve token
        print("\nLoading instrument master…", end=" ", flush=True)
        master = InstrumentMaster()
        master.load()
        token = master.get_token(exchange, symbol)
        if not token:
            print(f"Symbol {symbol!r} not found on {exchange}.", file=sys.stderr)
            sys.exit(1)
        print(f"token={token}")

        # Fetch candles
        print("Fetching candles from AngelOne API…")
        df = _fetch_all_candles(session, exchange, token, interval, from_date, to_date)

        # Run strategy
        print(f"Replaying {len(df)} bars through EMA {fast}/{slow} crossover…")
        trades = _run_ema_crossover(
            df=df,
            fast=fast, slow=slow,
            capital=capital,
            sl_points=sl_points,
            tp_points=tp_points,
            max_qty=max_qty,
            max_risk_pct=max_risk_pct,
            squareoff_hour=sq_h,
            squareoff_min=sq_m,
            exchange=exchange,
            daily_loss_limit=daily_loss_limit,
            max_trades_per_day=max_trades_per_day,
            max_consecutive_losses=max_consecutive_losses,
            tsl_enabled=tsl_enabled,
            tsl_mode=tsl_mode,
            tsl_value=tsl_value,
            tsl_activation_gap=tsl_activation_gap,
            tsl_atr_period=tsl_atr_period,
        )

        _print_report(trades, capital, symbol, interval)

    finally:
        try:
            session.logout()
        except Exception:
            pass


if __name__ == "__main__":
    main()
