"""
indicators/divergence.py — Divergence Detection
=================================================
Detects bullish and bearish divergences between price and an oscillator
(e.g., RSI, MACD histogram, OBV).
"""
import pandas as pd
import numpy as np


def _swing_highs(series: pd.Series, order: int = 5) -> pd.Series:
    """Mark local swing highs (higher than `order` bars on each side)."""
    result = pd.Series(False, index=series.index)
    vals = series.values
    for i in range(order, len(vals) - order):
        if np.isnan(vals[i]):
            continue
        is_high = True
        for j in range(1, order + 1):
            if vals[i] < vals[i - j] or vals[i] < vals[i + j]:
                is_high = False
                break
        result.iloc[i] = is_high
    return result


def _swing_lows(series: pd.Series, order: int = 5) -> pd.Series:
    """Mark local swing lows (lower than `order` bars on each side)."""
    result = pd.Series(False, index=series.index)
    vals = series.values
    for i in range(order, len(vals) - order):
        if np.isnan(vals[i]):
            continue
        is_low = True
        for j in range(1, order + 1):
            if vals[i] > vals[i - j] or vals[i] > vals[i + j]:
                is_low = False
                break
        result.iloc[i] = is_low
    return result


def bullish_divergence(
    price: pd.Series,
    oscillator: pd.Series,
    order: int = 5,
    lookback: int = 30,
) -> pd.Series:
    """
    Bullish divergence: price makes a lower low but oscillator makes a higher low.
    Signals potential upward reversal.

    Args:
        price: typically close or low prices
        oscillator: RSI, MACD histogram, or any oscillator
        order: swing detection window (bars on each side)
        lookback: max bars between two swing lows to compare

    Returns:
        Boolean Series — True on bars where bullish divergence is detected.
    """
    result = pd.Series(False, index=price.index)
    price_lows = _swing_lows(price, order)
    osc_lows = _swing_lows(oscillator, order)

    low_indices = price_lows[price_lows].index.tolist()

    for i in range(1, len(low_indices)):
        curr_idx = low_indices[i]
        prev_idx = low_indices[i - 1]

        curr_loc = price.index.get_loc(curr_idx)
        prev_loc = price.index.get_loc(prev_idx)
        if curr_loc - prev_loc > lookback:
            continue

        # Price: lower low
        if price[curr_idx] >= price[prev_idx]:
            continue

        # Oscillator: higher low (divergence)
        if oscillator[curr_idx] <= oscillator[prev_idx]:
            continue

        result[curr_idx] = True

    return result


def bearish_divergence(
    price: pd.Series,
    oscillator: pd.Series,
    order: int = 5,
    lookback: int = 30,
) -> pd.Series:
    """
    Bearish divergence: price makes a higher high but oscillator makes a lower high.
    Signals potential downward reversal.

    Args:
        price: typically close or high prices
        oscillator: RSI, MACD histogram, or any oscillator
        order: swing detection window (bars on each side)
        lookback: max bars between two swing highs to compare

    Returns:
        Boolean Series — True on bars where bearish divergence is detected.
    """
    result = pd.Series(False, index=price.index)
    price_highs = _swing_highs(price, order)

    high_indices = price_highs[price_highs].index.tolist()

    for i in range(1, len(high_indices)):
        curr_idx = high_indices[i]
        prev_idx = high_indices[i - 1]

        curr_loc = price.index.get_loc(curr_idx)
        prev_loc = price.index.get_loc(prev_idx)
        if curr_loc - prev_loc > lookback:
            continue

        # Price: higher high
        if price[curr_idx] <= price[prev_idx]:
            continue

        # Oscillator: lower high (divergence)
        if oscillator[curr_idx] >= oscillator[prev_idx]:
            continue

        result[curr_idx] = True

    return result
