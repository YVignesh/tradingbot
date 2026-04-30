"""
indicators/volatility.py — Volatility Indicators
==================================================
Bollinger Bands, ATR (Average True Range).

All functions:
  - Accept pandas Series or high/low/close Series
  - Return pandas Series or named tuple of Series
  - Have no broker dependency
"""
from typing import NamedTuple, Tuple
import math
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


def supertrend(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 10,
    multiplier: float = 3.0,
) -> Tuple[pd.Series, pd.Series]:
    """
    Supertrend indicator (ATR-based trend-following).

    Returns (line, direction):
      line      — the Supertrend price level (acts as dynamic support/resistance)
      direction — +1 when bullish (price above line), -1 when bearish (price below)

    A flip from -1 → +1 is a BUY signal; +1 → -1 is a SELL/SHORT signal.

    Args:
        high       : high price series
        low        : low price series
        close      : close price series
        period     : ATR lookback period (default 10)
        multiplier : ATR multiplier for band width (default 3.0)

    Returns:
        Tuple of (supertrend_line, direction) as pandas Series

    Example:
        st_line, st_dir = supertrend(df['high'], df['low'], df['close'])
        buy_signal  = (st_dir == 1) & (st_dir.shift(1) == -1)
        sell_signal = (st_dir == -1) & (st_dir.shift(1) == 1)
    """
    atr_vals = atr(high, low, close, period)
    hl2 = (high + low) / 2.0

    import math

    n = len(close)
    close_arr = close.values
    hl2_arr = hl2.values
    atr_arr = atr_vals.values

    final_upper = [float("nan")] * n
    final_lower = [float("nan")] * n
    st_line = [float("nan")] * n
    direction = [0] * n

    for i in range(n):
        a = atr_arr[i]
        if math.isnan(a):
            continue

        bu = hl2_arr[i] + multiplier * a
        bl = hl2_arr[i] - multiplier * a

        if i == 0 or math.isnan(final_upper[i - 1]):
            final_upper[i] = bu
            final_lower[i] = bl
            st_line[i] = bu
            direction[i] = -1
            continue

        # Ratchet upper band down
        final_upper[i] = bu if bu < final_upper[i - 1] or close_arr[i - 1] > final_upper[i - 1] else final_upper[i - 1]
        # Ratchet lower band up
        final_lower[i] = bl if bl > final_lower[i - 1] or close_arr[i - 1] < final_lower[i - 1] else final_lower[i - 1]

        # Determine direction and line
        prev_dir = direction[i - 1]
        if prev_dir == -1:
            if close_arr[i] > final_upper[i]:
                direction[i] = 1
                st_line[i] = final_lower[i]
            else:
                direction[i] = -1
                st_line[i] = final_upper[i]
        else:
            if close_arr[i] < final_lower[i]:
                direction[i] = -1
                st_line[i] = final_upper[i]
            else:
                direction[i] = 1
                st_line[i] = final_lower[i]

    idx = close.index
    return (
        pd.Series(st_line, index=idx, dtype=float),
        pd.Series(direction, index=idx, dtype=int),
    )
