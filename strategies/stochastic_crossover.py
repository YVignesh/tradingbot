"""
strategies/stochastic_crossover.py — Stochastic Oscillator crossover strategy
==============================================================================
Enters long when %K crosses above %D from the oversold zone (<= stoch_oversold).
Enters short when %K crosses below %D from the overbought zone (>= stoch_overbought).
Uses a trend EMA to filter trades in the direction of the prevailing trend.

config.json strategy options:
  trend_ema         : trend filter EMA period (default 50)
  stoch_k           : Stochastic %K period (default 14)
  stoch_d           : Stochastic %D smoothing period (default 3)
  stoch_oversold    : oversold threshold — enter long on cross from here (default 25)
  stoch_overbought  : overbought threshold — enter short on cross from here (default 75)
  stoch_exit_long   : exit long when %K reaches this level (default 80)
  stoch_exit_short  : exit short when %K falls to this level (default 20)
"""

from __future__ import annotations

import pandas as pd

from indicators.momentum import stochastic
from indicators.trend import ema
from strategies.directional import DirectionalStrategy


class StochasticCrossoverStrategy(DirectionalStrategy):
    NAME = "stochastic_crossover"

    def __init__(self, config: dict):
        super().__init__(config)
        strat = config["strategy"]
        self.trend_ema_period = int(strat.get("trend_ema", 50))
        self.k_period = int(strat.get("stoch_k", 14))
        self.d_period = int(strat.get("stoch_d", 3))
        self.oversold = float(strat.get("stoch_oversold", 25))
        self.overbought = float(strat.get("stoch_overbought", 75))
        self.exit_long = float(strat.get("stoch_exit_long", 80))
        self.exit_short = float(strat.get("stoch_exit_short", 20))

    def required_history_bars(self) -> int:
        return max(self.trend_ema_period, self.k_period + self.d_period) + 5

    def prepare_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        prepared = df.copy()
        s = stochastic(
            prepared["high"], prepared["low"], prepared["close"],
            k_period=self.k_period, d_period=self.d_period,
        )
        prepared["stoch_k"] = s.k
        prepared["stoch_d"] = s.d
        prepared["trend_ema"] = ema(prepared["close"], self.trend_ema_period)
        return prepared

    def signal_from_prepared(self, df: pd.DataFrame, index: int, direction: str):
        if index < 1:
            return None

        k_now = float(df["stoch_k"].iloc[index])
        k_prev = float(df["stoch_k"].iloc[index - 1])
        d_now = float(df["stoch_d"].iloc[index])
        d_prev = float(df["stoch_d"].iloc[index - 1])
        close = float(df["close"].iloc[index])
        trend = float(df["trend_ema"].iloc[index])

        # %K crosses above %D from oversold
        k_cross_up = k_now > d_now and k_prev <= d_prev and k_now <= self.oversold + 15
        # %K crosses below %D from overbought
        k_cross_down = k_now < d_now and k_prev >= d_prev and k_now >= self.overbought - 15

        if direction == "LONG":
            if k_now >= self.exit_long or close < trend:
                return "SELL"
            return None

        if direction == "SHORT":
            if k_now <= self.exit_short or close > trend:
                return "COVER"
            return None

        # Flat — look for entries
        if k_cross_up and close > trend:
            return "BUY"
        if k_cross_down and close < trend:
            return "SHORT"
        return None

    def describe_bar(self, df: pd.DataFrame, index: int) -> str:
        close = float(df["close"].iloc[index])
        k = float(df["stoch_k"].iloc[index])
        d = float(df["stoch_d"].iloc[index])
        trend = float(df["trend_ema"].iloc[index])
        zone = "OVERSOLD" if k < self.oversold else ("OVERBOUGHT" if k > self.overbought else "NEUTRAL")
        return (
            f"close=₹{close:.2f} %K={k:.1f} %D={d:.1f} "
            f"EMA{self.trend_ema_period}={trend:.2f} zone={zone}"
        )
