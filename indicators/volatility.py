"""
indicators/volatility.py — Volatility Indicators
==================================================
Bollinger Bands, ATR (Average True Range).

All functions:
  - Accept pandas Series or high/low/close Series
  - Return pandas Series or named tuple of Series
  - Have no broker dependency
"""
from typing import NamedTuple
import pandas as pd


class BollingerBands(NamedTuple):
    upper:  pd.Series   # upper band  (middle + k * std)
    middle: pd.Series   # middle band (SMA)
    lower:  pd.Series   # lower band  (middle - k * std)
    width:  pd.Series   # (upper - lower) / middle — normalised bandwidth


def bollinger_bands(
    series:  pd.Series,
    period:  int   = 20,
    std_dev: float = 2.0,
) -> BollingerBands:
    """
    Bollinger Bands.

    Middle band = SMA(period).
    Upper / lower bands = middle ± std_dev * rolling std.
    Width = (upper - lower) / middle — useful for squeeze detection.

    Args:
        series  : price series (typically close)
        period  : SMA + std lookback (default 20)
        std_dev : number of standard deviations for band width (default 2.0)

    Returns:
        BollingerBands(upper, middle, lower, width)

    Example:
        bb = bollinger_bands(df['close'])
        # Price touching lower band in uptrend → potential long entry
        touch_lower = df['close'] <= bb.lower
        # Squeeze: bands narrowing → breakout incoming
        squeeze = bb.width < bb.width.rolling(50).min() * 1.1
    """
    middle = series.rolling(window=period, min_periods=period).mean()
    std    = series.rolling(window=period, min_periods=period).std(ddof=0)
    upper  = middle + std_dev * std
    lower  = middle - std_dev * std
    width  = (upper - lower) / middle
    return BollingerBands(upper=upper, middle=middle, lower=lower, width=width)


def atr(
    high:   pd.Series,
    low:    pd.Series,
    close:  pd.Series,
    period: int = 14,
) -> pd.Series:
    """
    Average True Range (Wilder's smoothing).

    Measures market volatility independent of price direction.
    Use it for:
      - Dynamic stop-loss placement (e.g. SL = entry - 1.5 * ATR)
      - Position sizing (risk = ATR-based points per share)

    True Range = max(high-low, |high-prev_close|, |low-prev_close|)
    ATR = Wilder's EMA of True Range over `period` bars.

    Args:
        high   : high price series
        low    : low price series
        close  : close price series
        period : smoothing period (default 14)

    Returns:
        ATR series in rupees (same units as price)

    Example:
        df['atr'] = atr(df['high'], df['low'], df['close'])
        sl_price  = entry_price - 1.5 * df['atr'].iloc[-1]
    """
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
