"""
strategies/supertrend.py — Supertrend ATR trend-following strategy
===================================================================
Uses the Supertrend indicator (ATR-based dynamic support/resistance) for
trend-following entries and exits. A direction flip from bearish → bullish
fires BUY (or COVER if short); bullish → bearish fires SELL (or SHORT if flat).

config.json strategy options:
  supertrend_period     : ATR period for Supertrend (default 10)
  supertrend_multiplier : ATR multiplier for band width (default 3.0)
"""

from __future__ import annotations

import pandas as pd

from indicators.volatility import supertrend as compute_supertrend
from strategies.directional import DirectionalStrategy


class SupertrendStrategy(DirectionalStrategy):
    NAME = "supertrend"

    def __init__(self, config: dict):
        super().__init__(config)
        strat = config["strategy"]
        self.st_period = int(strat.get("supertrend_period", 10))
        self.st_mult = float(strat.get("supertrend_multiplier", 3.0))

    def required_history_bars(self) -> int:
        return self.st_period + 5

    def prepare_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        prepared = df.copy()
        st_line, st_dir = compute_supertrend(
            prepared["high"], prepared["low"], prepared["close"],
            period=self.st_period, multiplier=self.st_mult,
        )
        prepared["st_line"] = st_line
        prepared["st_dir"] = st_dir
        return prepared

    def signal_from_prepared(self, df: pd.DataFrame, index: int, direction: str):
        if index < 1:
            return None

        curr_dir = int(df["st_dir"].iloc[index])
        prev_dir = int(df["st_dir"].iloc[index - 1])

        if curr_dir == 0 or prev_dir == 0:
            return None

        flipped_bullish = curr_dir == 1 and prev_dir == -1
        flipped_bearish = curr_dir == -1 and prev_dir == 1

        if flipped_bullish:
            if direction == "SHORT":
                return "COVER"
            if direction == "FLAT":
                return "BUY"
        elif flipped_bearish:
            if direction == "LONG":
                return "SELL"
            if direction == "FLAT":
                return "SHORT"
        return None

    def describe_bar(self, df: pd.DataFrame, index: int) -> str:
        close = float(df["close"].iloc[index])
        st_line = float(df["st_line"].iloc[index])
        st_dir = int(df["st_dir"].iloc[index])
        trend = "BULL" if st_dir == 1 else "BEAR"
        return f"close=₹{close:.2f} ST={st_line:.2f} trend={trend}"
