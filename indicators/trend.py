"""
indicators/trend.py — Trend Indicators
========================================
EMA, SMA, DEMA, TEMA, and crossover detection helpers.

All functions:
  - Accept a pandas Series (e.g. df['close'])
  - Return a pandas Series with the same index
  - Produce NaN for initial bars where the window is incomplete
  - Have no broker dependency
"""
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    """
    Exponential Moving Average.

    Uses pandas ewm with adjust=False (Wilder-style recursive EMA),
    matching the calculation used by TradingView and most charting platforms.

    Args:
        series : price series (typically close)
        period : lookback period (e.g. 9, 21, 50, 200)

    Returns:
        EMA series (NaN for first `period - 1` bars)

    Example:
        df['ema9']  = ema(df['close'], 9)
        df['ema21'] = ema(df['close'], 21)
    """
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    """
    Simple Moving Average.

    Args:
        series : price series
        period : lookback period

    Returns:
        SMA series (NaN for first `period - 1` bars)

    Example:
        df['sma20'] = sma(df['close'], 20)
    """
    return series.rolling(window=period, min_periods=period).mean()


def dema(series: pd.Series, period: int) -> pd.Series:
    """
    Double Exponential Moving Average.
    DEMA = 2 * EMA(n) - EMA(EMA(n))

    Reduces lag compared to a plain EMA of the same period.

    Args:
        series : price series
        period : lookback period

    Returns:
        DEMA series

    Example:
        df['dema9'] = dema(df['close'], 9)
    """
    e = ema(series, period)
    return 2 * e - ema(e, period)


def tema(series: pd.Series, period: int) -> pd.Series:
    """
    Triple Exponential Moving Average.
    TEMA = 3*EMA - 3*EMA(EMA) + EMA(EMA(EMA))

    Even faster response than DEMA; useful for short-term momentum.

    Args:
        series : price series
        period : lookback period

    Returns:
        TEMA series

    Example:
        df['tema9'] = tema(df['close'], 9)
    """
    e1 = ema(series, period)
    e2 = ema(e1, period)
    e3 = ema(e2, period)
    return 3 * e1 - 3 * e2 + e3


def crossover(fast: pd.Series, slow: pd.Series) -> pd.Series:
    """
    Detect bars where `fast` crosses ABOVE `slow`.

    Returns a boolean Series — True only on the exact bar of the crossover.
    Use this to generate BUY signals in your strategy.

    Args:
        fast : faster moving series (e.g. EMA 9)
        slow : slower moving series (e.g. EMA 21)

    Returns:
        Boolean Series — True on crossover bars, False elsewhere

    Example:
        buy_signal = crossover(df['ema9'], df['ema21'])
        if buy_signal.iloc[-1]:
            enter_long(...)
    """
    return (fast > slow) & (fast.shift(1) <= slow.shift(1))


def crossunder(fast: pd.Series, slow: pd.Series) -> pd.Series:
    """
    Detect bars where `fast` crosses BELOW `slow`.

    Returns a boolean Series — True only on the exact bar of the crossunder.
    Use this to generate SELL signals in your strategy.

    Args:
        fast : faster moving series (e.g. EMA 9)
        slow : slower moving series (e.g. EMA 21)

    Returns:
        Boolean Series — True on crossunder bars, False elsewhere

    Example:
        sell_signal = crossunder(df['ema9'], df['ema21'])
        if sell_signal.iloc[-1]:
            exit_long(...)
    """
    return (fast < slow) & (fast.shift(1) >= slow.shift(1))


def adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Average Directional Index (ADX) with +DI and -DI.

    Measures trend strength regardless of direction:
      - ADX < 20  → no trend / chop
      - ADX 20–40 → developing trend
      - ADX > 40  → strong trend

    Uses Wilder's smoothing (same as ATR).

    Args:
        high   : high price series
        low    : low price series
        close  : close price series
        period : smoothing period (default 14)

    Returns:
        (adx, plus_di, minus_di) — three pd.Series
    """
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    # True Range
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    # Directional Movement
    up_move = high - prev_high
    down_move = prev_low - low
    plus_dm = pd.Series(0.0, index=high.index)
    minus_dm = pd.Series(0.0, index=high.index)
    plus_dm[(up_move > down_move) & (up_move > 0)] = up_move
    minus_dm[(down_move > up_move) & (down_move > 0)] = down_move

    # Wilder smoothing (EMA with alpha = 1/period)
    alpha = 1.0 / period
    atr = tr.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    smooth_plus = plus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    smooth_minus = minus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean()

    plus_di = 100.0 * smooth_plus / atr
    minus_di = 100.0 * smooth_minus / atr

    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx_line = dx.ewm(alpha=alpha, min_periods=period, adjust=False).mean()

    return adx_line, plus_di, minus_di
