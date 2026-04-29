"""
screener/filters.py — Symbol filtering and metric extraction
============================================================
Fetches daily candles and applies liquidity / volatility filters.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from broker.market_data import candles_to_dataframe, get_candles
from indicators.volatility import atr
from utils import get_logger

_log = get_logger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))


def evaluate_symbol(
    session,
    *,
    exchange: str,
    symbol: str,
    token: str,
    lookback_days: int,
    min_price: float,
    max_price: float,
    min_avg_volume: float,
    min_atr: float,
    max_atr: float,
    max_gap_pct: float,
    screener=None,
) -> Optional[dict]:
    to_date = datetime.now(IST).strftime("%Y-%m-%d %H:%M")
    from_date = (datetime.now(IST) - timedelta(days=lookback_days)).strftime("%Y-%m-%d 09:15")

    candles = get_candles(
        session,
        exchange,
        token,
        "ONE_DAY",
        from_date,
        to_date,
    )
    if not candles:
        return None

    df = candles_to_dataframe(candles)
    if len(df) < 25:
        return None

    prepared = df.copy()
    prepared["atr"] = atr(prepared["high"], prepared["low"], prepared["close"], 14)
    prepared["avg_volume_20"] = prepared["volume"].rolling(20, min_periods=20).mean()
    last = prepared.iloc[-1]
    prev_5 = prepared.iloc[-6] if len(prepared) >= 6 else prepared.iloc[0]
    prev_1 = prepared.iloc[-2]

    close = float(last["close"])
    avg_volume = float(last["avg_volume_20"])
    atr_value = float(last["atr"])
    volume_spike = float(last["volume"]) / avg_volume if avg_volume > 0 else 0.0
    momentum_5d = ((close / float(prev_5["close"])) - 1.0) * 100 if float(prev_5["close"]) > 0 else 0.0
    gap_pct = abs((close / float(prev_1["close"])) - 1.0) * 100 if float(prev_1["close"]) > 0 else 0.0

    if min_price > 0 and close < min_price:
        return None
    if max_price > 0 and close > max_price:
        return None
    if min_avg_volume > 0 and avg_volume < min_avg_volume:
        return None
    if min_atr > 0 and atr_value < min_atr:
        return None
    if max_atr > 0 and atr_value > max_atr:
        return None
    if max_gap_pct > 0 and gap_pct > max_gap_pct:
        return None

    metrics = {
        "exchange": exchange,
        "symbol": symbol,
        "close": round(close, 2),
        "avg_volume_20": round(avg_volume, 2),
        "atr": round(atr_value, 2),
        "momentum_5d": round(momentum_5d, 2),
        "volume_spike": round(volume_spike, 2),
        "gap_pct": round(gap_pct, 2),
    }

    if screener is not None:
        metrics.update(screener.extra_metrics(prepared))
        if not screener.passes_filter(metrics):
            return None

    _log.debug("Screener kept %s:%s metrics=%s", exchange, symbol, metrics)
    return metrics
