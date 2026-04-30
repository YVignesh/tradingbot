"""
indicators/patterns.py — Candlestick Pattern Detection
========================================================
Detects common candlestick patterns from OHLC data.
Returns boolean Series marking bars where patterns appear.
"""
import pandas as pd


def inside_bar(high: pd.Series, low: pd.Series) -> pd.Series:
    """
    Inside bar: current bar's range is entirely within the previous bar's range.
    high[i] <= high[i-1] AND low[i] >= low[i-1].
    """
    return (high <= high.shift(1)) & (low >= low.shift(1))


def outside_bar(high: pd.Series, low: pd.Series) -> pd.Series:
    """
    Outside bar (engulfing range): current bar's range engulfs previous bar.
    high[i] > high[i-1] AND low[i] < low[i-1].
    """
    return (high > high.shift(1)) & (low < low.shift(1))


def nr7(high: pd.Series, low: pd.Series, lookback: int = 7) -> pd.Series:
    """
    NR7: Narrowest Range of last `lookback` bars.
    Current bar's range is the smallest in the last 7 bars.
    """
    bar_range = high - low
    min_range = bar_range.rolling(lookback, min_periods=lookback).min()
    return bar_range == min_range


def bullish_engulfing(
    open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series
) -> pd.Series:
    """
    Bullish engulfing: prior bar is bearish, current bar is bullish and
    engulfs the prior bar's body.
    """
    prev_bearish = close.shift(1) < open_.shift(1)
    curr_bullish = close > open_
    engulfs = (open_ <= close.shift(1)) & (close >= open_.shift(1))
    return prev_bearish & curr_bullish & engulfs


def bearish_engulfing(
    open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series
) -> pd.Series:
    """
    Bearish engulfing: prior bar is bullish, current bar is bearish and
    engulfs the prior bar's body.
    """
    prev_bullish = close.shift(1) > open_.shift(1)
    curr_bearish = close < open_
    engulfs = (open_ >= close.shift(1)) & (close <= open_.shift(1))
    return prev_bullish & curr_bearish & engulfs


def hammer(
    open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series,
    body_ratio: float = 0.3, wick_ratio: float = 2.0,
) -> pd.Series:
    """
    Hammer: small body at the top of the range with a long lower wick.
    body_ratio: max body size as fraction of total range.
    wick_ratio: min lower_wick / body ratio.
    """
    total_range = high - low
    body = (close - open_).abs()
    upper_body = pd.concat([open_, close], axis=1).max(axis=1)
    lower_wick = pd.concat([open_, close], axis=1).min(axis=1) - low

    body_small = body <= total_range * body_ratio
    wick_long = lower_wick >= body * wick_ratio
    upper_wick_small = (high - upper_body) <= body * 0.5
    valid_range = total_range > 0

    return body_small & wick_long & upper_wick_small & valid_range


def shooting_star(
    open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series,
    body_ratio: float = 0.3, wick_ratio: float = 2.0,
) -> pd.Series:
    """
    Shooting star: small body at the bottom with a long upper wick.
    Bearish reversal pattern (mirror of hammer).
    """
    total_range = high - low
    body = (close - open_).abs()
    lower_body = pd.concat([open_, close], axis=1).min(axis=1)
    upper_wick = high - pd.concat([open_, close], axis=1).max(axis=1)

    body_small = body <= total_range * body_ratio
    wick_long = upper_wick >= body * wick_ratio
    lower_wick_small = (lower_body - low) <= body * 0.5
    valid_range = total_range > 0

    return body_small & wick_long & lower_wick_small & valid_range


def doji(
    open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series,
    threshold: float = 0.05,
) -> pd.Series:
    """
    Doji: open and close are nearly equal (body < threshold × range).
    """
    total_range = high - low
    body = (close - open_).abs()
    return (body <= total_range * threshold) & (total_range > 0)


def mother_bar_range(high: pd.Series, low: pd.Series) -> tuple[pd.Series, pd.Series]:
    """
    For inside bar breakout: returns the mother bar (previous bar) high and low.
    Only meaningful where inside_bar() is True.
    """
    return high.shift(1), low.shift(1)
