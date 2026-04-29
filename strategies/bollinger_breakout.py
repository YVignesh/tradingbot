"""
strategies/bollinger_breakout.py — Squeeze breakout strategy
=============================================================
Trades expansion out of compressed volatility when price breaks the
band with confirming volume, then exits on a mean-reversion failure.
"""

from __future__ import annotations

import pandas as pd

from indicators.volatility import bollinger_bands
from strategies.directional import DirectionalStrategy


class BollingerBreakoutStrategy(DirectionalStrategy):
    NAME = "bollinger_breakout"

    def __init__(self, config: dict):
        super().__init__(config)
        strat = config["strategy"]
        self.bb_period = int(strat.get("bb_period", 20))
        self.bb_std_dev = float(strat.get("bb_std_dev", 2.0))
        self.squeeze_lookback = int(strat.get("squeeze_lookback", 50))
        self.squeeze_threshold = float(strat.get("squeeze_threshold", 1.15))
        self.volume_period = int(strat.get("volume_period", 20))
        self.volume_spike = float(strat.get("volume_spike", 1.5))

    def required_history_bars(self) -> int:
        return max(self.bb_period + 2, self.squeeze_lookback + 2, self.volume_period + 2)

    def prepare_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        prepared = df.copy()
        bands = bollinger_bands(
            prepared["close"],
            period=self.bb_period,
            std_dev=self.bb_std_dev,
        )
        prepared["bb_upper"] = bands.upper
        prepared["bb_middle"] = bands.middle
        prepared["bb_lower"] = bands.lower
        prepared["bb_width"] = bands.width
        prepared["bb_width_min"] = prepared["bb_width"].rolling(
            self.squeeze_lookback,
            min_periods=self.squeeze_lookback,
        ).min()
        prepared["volume_avg"] = prepared["volume"].rolling(
            self.volume_period,
            min_periods=self.volume_period,
        ).mean()
        return prepared

    def signal_from_prepared(self, df: pd.DataFrame, index: int, direction: str):
        close = float(df["close"].iloc[index])
        upper = float(df["bb_upper"].iloc[index])
        middle = float(df["bb_middle"].iloc[index])
        lower = float(df["bb_lower"].iloc[index])
        width = float(df["bb_width"].iloc[index])
        width_min = float(df["bb_width_min"].iloc[index])
        volume = float(df["volume"].iloc[index])
        volume_avg = float(df["volume_avg"].iloc[index])

        squeeze_on = width_min > 0 and width <= width_min * self.squeeze_threshold
        volume_confirm = volume_avg > 0 and volume >= volume_avg * self.volume_spike

        if direction == "LONG":
            if close < middle:
                return "SELL"
            return None
        if direction == "SHORT":
            if close > middle:
                return "COVER"
            return None
        if squeeze_on and volume_confirm and close > upper:
            return "BUY"
        if squeeze_on and volume_confirm and close < lower:
            return "SHORT"
        return None

    def describe_bar(self, df: pd.DataFrame, index: int) -> str:
        close = float(df["close"].iloc[index])
        upper = float(df["bb_upper"].iloc[index])
        lower = float(df["bb_lower"].iloc[index])
        width = float(df["bb_width"].iloc[index])
        volume = float(df["volume"].iloc[index])
        volume_avg = float(df["volume_avg"].iloc[index])
        return (
            f"close=₹{close:.2f} BB[{lower:.2f},{upper:.2f}] width={width:.3f} "
            f"vol={volume:.0f}/{volume_avg:.0f}"
        )
