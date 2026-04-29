"""
strategies/three_ema_trend.py — Triple EMA Alignment trend strategy
====================================================================
Requires all three EMAs to be stacked in the correct order before entering.
Entry fires on the first bar where the fast EMA crosses the mid EMA while
all three are aligned (fast > mid > slow for longs, reversed for shorts).
Exit when the fast EMA crosses back through the mid EMA against the trade.

Inspired by the "3-EMA pullback" setup popular among Indian positional traders.

config.json strategy options:
  ema_fast : fast EMA period (default 8)
  ema_mid  : middle EMA period (default 21)
  ema_slow : slow EMA period (default 55)
"""

from __future__ import annotations

import pandas as pd

from indicators.trend import ema
from strategies.directional import DirectionalStrategy


class ThreeEmaTrendStrategy(DirectionalStrategy):
    NAME = "three_ema_trend"

    def __init__(self, config: dict):
        super().__init__(config)
        strat = config["strategy"]
        self.fast = int(strat.get("ema_fast", 8))
        self.mid = int(strat.get("ema_mid", 21))
        self.slow = int(strat.get("ema_slow", 55))

    def required_history_bars(self) -> int:
        return self.slow + 5

    def prepare_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        prepared = df.copy()
        prepared["ema_fast"] = ema(prepared["close"], self.fast)
        prepared["ema_mid"] = ema(prepared["close"], self.mid)
        prepared["ema_slow"] = ema(prepared["close"], self.slow)
        return prepared

    def signal_from_prepared(self, df: pd.DataFrame, index: int, direction: str):
        if index < 1:
            return None

        ef_now = float(df["ema_fast"].iloc[index])
        em_now = float(df["ema_mid"].iloc[index])
        es_now = float(df["ema_slow"].iloc[index])
        ef_prev = float(df["ema_fast"].iloc[index - 1])
        em_prev = float(df["ema_mid"].iloc[index - 1])

        all_bull = ef_now > em_now > es_now
        all_bear = ef_now < em_now < es_now

        fast_crossed_up = ef_now > em_now and ef_prev <= em_prev
        fast_crossed_down = ef_now < em_now and ef_prev >= em_prev

        if direction == "LONG":
            # Exit when fast crosses below mid
            if fast_crossed_down:
                return "SELL"
            return None

        if direction == "SHORT":
            # Exit when fast crosses above mid
            if fast_crossed_up:
                return "COVER"
            return None

        # Flat — enter on first cross with full alignment
        if all_bull and fast_crossed_up:
            return "BUY"
        if all_bear and fast_crossed_down:
            return "SHORT"
        return None

    def describe_bar(self, df: pd.DataFrame, index: int) -> str:
        ef = float(df["ema_fast"].iloc[index])
        em = float(df["ema_mid"].iloc[index])
        es = float(df["ema_slow"].iloc[index])
        close = float(df["close"].iloc[index])
        if ef > em > es:
            stack = "BULL"
        elif ef < em < es:
            stack = "BEAR"
        else:
            stack = "MIXED"
        return (
            f"close=₹{close:.2f} EMA{self.fast}={ef:.2f} "
            f"EMA{self.mid}={em:.2f} EMA{self.slow}={es:.2f} stack={stack}"
        )
