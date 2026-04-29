"""
strategies/rsi_reversal.py — RSI Mean Reversion strategy
=========================================================
Fades extreme RSI readings with a trend filter.
  LONG  when RSI exits oversold (crosses above rsi_oversold) AND price > trend EMA
  SHORT when RSI exits overbought (crosses below rsi_overbought) AND price < trend EMA
  SELL  when RSI reaches rsi_exit_long  (overbought zone) or trend flips bearish
  COVER when RSI reaches rsi_exit_short (oversold zone) or trend flips bullish

config.json strategy options:
  trend_ema         : trend filter EMA period (default 50)
  rsi_period        : RSI lookback period (default 14)
  rsi_oversold      : oversold level — enter long when RSI crosses above (default 30)
  rsi_overbought    : overbought level — enter short when RSI crosses below (default 70)
  rsi_exit_long     : exit long when RSI reaches this (default 65)
  rsi_exit_short    : exit short when RSI reaches this (default 35)
"""

from __future__ import annotations

import pandas as pd

from indicators.momentum import rsi
from indicators.trend import ema
from strategies.directional import DirectionalStrategy


class RsiReversalStrategy(DirectionalStrategy):
    NAME = "rsi_reversal"

    def __init__(self, config: dict):
        super().__init__(config)
        strat = config["strategy"]
        self.trend_ema_period = int(strat.get("trend_ema", 50))
        self.rsi_period = int(strat.get("rsi_period", 14))
        self.rsi_oversold = float(strat.get("rsi_oversold", 30))
        self.rsi_overbought = float(strat.get("rsi_overbought", 70))
        self.rsi_exit_long = float(strat.get("rsi_exit_long", 65))
        self.rsi_exit_short = float(strat.get("rsi_exit_short", 35))

    def required_history_bars(self) -> int:
        return max(self.trend_ema_period, self.rsi_period) + 5

    def prepare_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        prepared = df.copy()
        prepared["rsi"] = rsi(prepared["close"], self.rsi_period)
        prepared["trend_ema"] = ema(prepared["close"], self.trend_ema_period)
        return prepared

    def signal_from_prepared(self, df: pd.DataFrame, index: int, direction: str):
        if index < 1:
            return None

        rsi_now = float(df["rsi"].iloc[index])
        rsi_prev = float(df["rsi"].iloc[index - 1])
        close = float(df["close"].iloc[index])
        trend = float(df["trend_ema"].iloc[index])

        # RSI exits oversold (crosses above rsi_oversold)
        crossed_oversold_up = rsi_prev < self.rsi_oversold <= rsi_now

        # RSI exits overbought (crosses below rsi_overbought)
        crossed_overbought_down = rsi_prev > self.rsi_overbought >= rsi_now

        if direction == "LONG":
            if rsi_now >= self.rsi_exit_long or close < trend:
                return "SELL"
            return None

        if direction == "SHORT":
            if rsi_now <= self.rsi_exit_short or close > trend:
                return "COVER"
            return None

        # Flat — look for entries
        if crossed_oversold_up and close > trend:
            return "BUY"
        if crossed_overbought_down and close < trend:
            return "SHORT"
        return None

    def describe_bar(self, df: pd.DataFrame, index: int) -> str:
        close = float(df["close"].iloc[index])
        rsi_val = float(df["rsi"].iloc[index])
        trend = float(df["trend_ema"].iloc[index])
        regime = "BULL" if close > trend else "BEAR"
        return (
            f"close=₹{close:.2f} RSI={rsi_val:.1f} "
            f"EMA{self.trend_ema_period}={trend:.2f} regime={regime}"
        )
