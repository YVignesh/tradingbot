"""
indicators/momentum.py — Momentum Indicators
==============================================
RSI, MACD, Stochastic Oscillator.

All functions:
  - Accept pandas Series (close / high / low)
  - Return pandas Series or named tuple of Series
  - Have no broker dependency
"""
from typing import NamedTuple
import pandas as pd


# ── Return types ──────────────────────────────────────────────────────────────

class MACDResult(NamedTuple):
    macd:      pd.Series   # MACD line  (fast EMA - slow EMA)
    signal:    pd.Series   # Signal line (EMA of MACD)
    histogram: pd.Series   # MACD - Signal


class StochasticResult(NamedTuple):
    k: pd.Series   # %K — fast stochastic
    d: pd.Series   # %D — signal line (SMA of %K)


# ── Indicators ────────────────────────────────────────────────────────────────

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    Relative Strength Index (Wilder's smoothing method).

    Oscillates between 0 and 100:
      > 70 → overbought (potential reversal down)
      < 30 → oversold   (potential reversal up)

    Uses Wilder's EWM smoothing (alpha = 1/period, adjust=False),
    matching TradingView and most standard charting platforms.

    Args:
        series : close price series
        period : lookback period (default 14)

    Returns:
        RSI series (NaN for first `period` bars)

    Example:
        df['rsi'] = rsi(df['close'], 14)
        overbought = df['rsi'] > 70
    """
    delta    = series.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs       = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd(
    series:       pd.Series,
    fast_period:  int = 12,
    slow_period:  int = 26,
    signal_period:int = 9,
) -> MACDResult:
    """
    Moving Average Convergence Divergence.

    Standard settings: fast=12, slow=26, signal=9 (matches TradingView default).

    Args:
        series        : close price series
        fast_period   : fast EMA period (default 12)
        slow_period   : slow EMA period (default 26)
        signal_period : signal line EMA period (default 9)

    Returns:
        MACDResult(macd, signal, histogram)
          macd      — crosses above 0 = bullish momentum
          signal    — crossover of macd/signal = entry/exit trigger
          histogram — macd - signal; positive = bullish, negative = bearish

    Example:
        m = macd(df['close'])
        buy_signal  = (m.macd > m.signal) & (m.macd.shift(1) <= m.signal.shift(1))
        sell_signal = (m.macd < m.signal) & (m.macd.shift(1) >= m.signal.shift(1))
    """
    ema_fast   = series.ewm(span=fast_period,   adjust=False, min_periods=fast_period).mean()
    ema_slow   = series.ewm(span=slow_period,   adjust=False, min_periods=slow_period).mean()
    macd_line  = ema_fast - ema_slow
    signal_line= macd_line.ewm(span=signal_period, adjust=False, min_periods=signal_period).mean()
    histogram  = macd_line - signal_line
    return MACDResult(macd=macd_line, signal=signal_line, histogram=histogram)


def stochastic(
    high:     pd.Series,
    low:      pd.Series,
    close:    pd.Series,
    k_period: int = 14,
    d_period: int = 3,
) -> StochasticResult:
    """
    Stochastic Oscillator (%K and %D).

    %K measures where close sits within the recent high-low range (0–100).
    %D is a smoothed signal line of %K.

    Overbought: %K > 80; Oversold: %K < 20.
    A common entry signal: %K crosses above %D while below 20 (oversold crossover).

    Args:
        high     : high price series
        low      : low price series
        close    : close price series
        k_period : %K lookback window (default 14)
        d_period : %D smoothing period (default 3)

    Returns:
        StochasticResult(k, d)

    Example:
        s = stochastic(df['high'], df['low'], df['close'])
        oversold_cross = (s.k > s.d) & (s.k.shift(1) <= s.d.shift(1)) & (s.k < 20)
    """
    lowest_low    = low.rolling(k_period,  min_periods=k_period).min()
    highest_high  = high.rolling(k_period, min_periods=k_period).max()
    k = 100 * (close - lowest_low) / (highest_high - lowest_low)
    d = k.rolling(d_period, min_periods=d_period).mean()
    return StochasticResult(k=k, d=d)
