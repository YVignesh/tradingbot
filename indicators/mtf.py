"""
indicators/mtf.py — Multi-Timeframe Helpers
=============================================
Utilities for multi-timeframe analysis: resample intraday bars to
higher timeframes and compute higher-TF trend direction.
"""
import pandas as pd

from indicators.trend import ema, sma
from indicators.momentum import rsi, macd
from indicators.volatility import atr


# ── OHLCV Resampling ─────────────────────────────────────────────────────────

_RESAMPLE_MAP = {
    "5min_to_15min": "15min",
    "5min_to_30min": "30min",
    "5min_to_1h": "1h",
    "15min_to_1h": "1h",
    "15min_to_4h": "4h",
    "30min_to_1h": "1h",
    "30min_to_4h": "4h",
    "1h_to_4h": "4h",
    "1h_to_1d": "1D",
}


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """
    Resample intraday OHLCV to a higher timeframe.

    Args:
        df: DataFrame with DatetimeIndex and columns [open, high, low, close, volume]
        rule: pandas resample rule string (e.g. '15min', '1h', '4h', '1D')

    Returns:
        Resampled DataFrame with same OHLCV columns, NaN rows dropped.
    """
    resampled = df.resample(rule).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna(subset=["open"])
    return resampled


# ── Higher-TF Trend Direction ────────────────────────────────────────────────

def higher_tf_trend(
    df: pd.DataFrame,
    rule: str,
    ema_period: int = 21,
) -> pd.Series:
    """
    Compute trend direction on a higher timeframe and forward-fill it
    back onto the original (lower) timeframe index.

    Returns a Series with values: 1 (bullish), -1 (bearish), 0 (unknown).
    The value is forward-filled so each intraday bar carries the last
    completed higher-TF trend signal.

    Args:
        df: Intraday DataFrame with DatetimeIndex and OHLCV columns
        rule: Resample rule (e.g. '1h', '4h')
        ema_period: EMA period on the higher timeframe
    """
    htf = resample_ohlcv(df, rule)
    htf_ema = ema(htf["close"], ema_period)

    # Trend: bullish if close > EMA, bearish if close < EMA
    htf_trend = pd.Series(0, index=htf.index, dtype=int)
    htf_trend[htf["close"] > htf_ema] = 1
    htf_trend[htf["close"] < htf_ema] = -1

    # Reindex to original timeframe with forward-fill
    return htf_trend.reindex(df.index, method="ffill").fillna(0).astype(int)


def higher_tf_rsi(
    df: pd.DataFrame,
    rule: str,
    rsi_period: int = 14,
) -> pd.Series:
    """
    Compute RSI on a higher timeframe and forward-fill to original index.
    """
    htf = resample_ohlcv(df, rule)
    htf_rsi = rsi(htf["close"], rsi_period)
    return htf_rsi.reindex(df.index, method="ffill")


def higher_tf_ema(
    df: pd.DataFrame,
    rule: str,
    period: int = 21,
) -> pd.Series:
    """
    Compute EMA on a higher timeframe and forward-fill to original index.
    """
    htf = resample_ohlcv(df, rule)
    htf_ema_vals = ema(htf["close"], period)
    return htf_ema_vals.reindex(df.index, method="ffill")


# ── Convenience: check alignment ─────────────────────────────────────────────

def mtf_aligned(
    df: pd.DataFrame,
    ltf_bullish: pd.Series,
    htf_rule: str = "1h",
    ema_period: int = 21,
) -> pd.Series:
    """
    Returns True where the lower-timeframe signal is aligned with the
    higher-timeframe trend direction.

    ltf_bullish: boolean Series — True where lower TF says bullish.
    Returns: True where ltf_bullish AND htf_trend == 1.
    """
    htf = higher_tf_trend(df, htf_rule, ema_period)
    return ltf_bullish & (htf == 1)
