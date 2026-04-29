"""
strategies/macd_rsi_trend.py — Momentum confirmation trend strategy
====================================================================
Trend filter with a slow EMA, then MACD + RSI confirmation for entries.
This trades slower than raw EMA crossover and avoids a lot of chop.
"""

from __future__ import annotations

import pandas as pd

from indicators.momentum import macd, rsi
from indicators.trend import ema
from strategies.directional import DirectionalStrategy


class MacdRsiTrendStrategy(DirectionalStrategy):
    NAME = "macd_rsi_trend"

    def __init__(self, config: dict):
        super().__init__(config)
        strat = config["strategy"]
        self.trend_ema = int(strat.get("trend_ema", 50))
        self.macd_fast = int(strat.get("macd_fast", 12))
        self.macd_slow = int(strat.get("macd_slow", 26))
        self.macd_signal = int(strat.get("macd_signal", 9))
        self.rsi_period = int(strat.get("rsi_period", 14))
        self.rsi_long_threshold = float(strat.get("rsi_long_threshold", 55))
        self.rsi_short_threshold = float(strat.get("rsi_short_threshold", 45))
        self.rsi_exit_long = float(strat.get("rsi_exit_long", 50))
        self.rsi_exit_short = float(strat.get("rsi_exit_short", 50))

    def required_history_bars(self) -> int:
        return max(self.trend_ema, self.macd_slow + self.macd_signal, self.rsi_period) + 2

    def prepare_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        prepared = df.copy()
        prepared["trend_ema"] = ema(prepared["close"], self.trend_ema)
        prepared["rsi"] = rsi(prepared["close"], self.rsi_period)
        macd_result = macd(
            prepared["close"],
            fast_period=self.macd_fast,
            slow_period=self.macd_slow,
            signal_period=self.macd_signal,
        )
        prepared["macd"] = macd_result.macd
        prepared["macd_signal"] = macd_result.signal
        prepared["macd_hist"] = macd_result.histogram
        return prepared

    def signal_from_prepared(self, df: pd.DataFrame, index: int, direction: str):
        close = float(df["close"].iloc[index])
        trend_ema = float(df["trend_ema"].iloc[index])
        rsi_value = float(df["rsi"].iloc[index])
        macd_value = float(df["macd"].iloc[index])
        signal_value = float(df["macd_signal"].iloc[index])
        hist = float(df["macd_hist"].iloc[index])

        bullish = close > trend_ema and macd_value > signal_value and hist > 0 and rsi_value >= self.rsi_long_threshold
        bearish = close < trend_ema and macd_value < signal_value and hist < 0 and rsi_value <= self.rsi_short_threshold

        if direction == "LONG":
            if close < trend_ema or macd_value < signal_value or rsi_value < self.rsi_exit_long:
                return "SELL"
            return None
        if direction == "SHORT":
            if close > trend_ema or macd_value > signal_value or rsi_value > self.rsi_exit_short:
                return "COVER"
            return None
        if bullish:
            return "BUY"
        if bearish:
            return "SHORT"
        return None

    def describe_bar(self, df: pd.DataFrame, index: int) -> str:
        close = float(df["close"].iloc[index])
        trend_ema = float(df["trend_ema"].iloc[index])
        rsi_value = float(df["rsi"].iloc[index])
        macd_value = float(df["macd"].iloc[index])
        signal_value = float(df["macd_signal"].iloc[index])
        regime = "BULL" if close > trend_ema else "BEAR"
        return (
            f"close=₹{close:.2f} trendEMA={trend_ema:.2f} RSI={rsi_value:.1f} "
            f"MACD={macd_value:.3f}/{signal_value:.3f} regime={regime}"
        )
