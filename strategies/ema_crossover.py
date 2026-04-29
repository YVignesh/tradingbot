"""
strategies/ema_crossover.py — EMA state-following crossover strategy
=====================================================================
Keeps the original EMA bull/bear regime logic, now on top of the shared
directional state machine used by the stronger strategy variants too.
"""

from __future__ import annotations

import pandas as pd

from indicators.trend import ema
from strategies.directional import DirectionalStrategy


class EmaCrossoverStrategy(DirectionalStrategy):
    NAME = "ema_crossover"

    def __init__(self, config: dict):
        super().__init__(config)
        strat = config["strategy"]
        self.fast = int(strat.get("ema_fast", 9))
        self.slow = int(strat.get("ema_slow", 21))

    def required_history_bars(self) -> int:
        return self.slow + 2

    def prepare_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        prepared = df.copy()
        prepared["ema_fast"] = ema(prepared["close"], self.fast)
        prepared["ema_slow"] = ema(prepared["close"], self.slow)
        return prepared

    def signal_from_prepared(self, df: pd.DataFrame, index: int, direction: str):
        ema_bullish = bool(df["ema_fast"].iloc[index] > df["ema_slow"].iloc[index])
        if ema_bullish:
            if direction == "SHORT":
                return "COVER"
            if direction == "FLAT":
                return "BUY"
        else:
            if direction == "LONG":
                return "SELL"
            if direction == "FLAT":
                return "SHORT"
        return None

    def describe_bar(self, df: pd.DataFrame, index: int) -> str:
        last_fast = float(df["ema_fast"].iloc[index])
        last_slow = float(df["ema_slow"].iloc[index])
        last_close = float(df["close"].iloc[index])
        trend = "BULL" if last_fast > last_slow else "BEAR"
        return (
            f"close=₹{last_close:.2f} EMA{self.fast}={last_fast:.2f} "
            f"EMA{self.slow}={last_slow:.2f} trend={trend}"
        )
