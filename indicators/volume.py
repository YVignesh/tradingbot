"""
indicators/volume.py — Volume Indicators
==========================================
VWAP, OBV (On-Balance Volume).

All functions:
  - Accept pandas Series from candles_to_dataframe() output
  - Return pandas Series with the same index
  - Have no broker dependency
"""
import pandas as pd


def vwap(
    high:   pd.Series,
    low:    pd.Series,
    close:  pd.Series,
    volume: pd.Series,
    period: int = None,
) -> pd.Series:
    """
    Volume Weighted Average Price.

    VWAP = cumulative(typical_price * volume) / cumulative(volume)
    where typical_price = (high + low + close) / 3.

    Two modes:
      - period=None  : cumulative VWAP from the first bar (standard intraday VWAP).
                       Pass only today's candles for a proper daily VWAP reset.
      - period=int   : rolling VWAP over `period` bars (useful across sessions).

    Interpretation:
      Price above VWAP → bullish (institutional buyers active)
      Price below VWAP → bearish
      VWAP acts as dynamic support/resistance intraday.

    Args:
        high   : high price series
        low    : low price series
        close  : close price series
        volume : volume series
        period : rolling window (None = cumulative from first bar)

    Returns:
        VWAP series in rupees

    Example:
        # Intraday VWAP (pass today's candles only)
        today_candles = get_candles_today(session, "NSE", "3045")
        df = candles_to_dataframe(today_candles)
        df['vwap'] = vwap(df['high'], df['low'], df['close'], df['volume'])

        # Rolling VWAP (multi-session)
        df['vwap20'] = vwap(df['high'], df['low'], df['close'], df['volume'], period=20)
    """
    typical_price = (high + low + close) / 3
    tp_vol        = typical_price * volume

    if period is None:
        return tp_vol.cumsum() / volume.cumsum()
    else:
        return (
            tp_vol.rolling(window=period, min_periods=period).sum()
            / volume.rolling(window=period, min_periods=period).sum()
        )


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """
    On-Balance Volume.

    Accumulates volume in the direction of price movement:
      close > prev_close → add volume
      close < prev_close → subtract volume
      close == prev_close → no change

    OBV divergence from price is a leading indicator of reversals:
      Price making new highs but OBV falling → distribution (bearish)
      Price making new lows but OBV rising  → accumulation (bullish)

    Args:
        close  : close price series
        volume : volume series

    Returns:
        OBV series (cumulative, starts at 0)

    Example:
        df['obv'] = obv(df['close'], df['volume'])
        # OBV rising with price = confirmed trend
        # OBV falling while price rises = potential reversal
    """
    direction = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    direction.iloc[0] = 0
    return (volume * direction).cumsum()
