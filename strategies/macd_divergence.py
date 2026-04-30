"""
strategies/macd_divergence.py — MACD Divergence Strategy
=========================================================
Detects bullish/bearish divergences between price and MACD histogram
with trend filter (EMA) and optional volume confirmation.

  BUY:   bullish divergence detected AND close > EMA(trend_ema)
  SHORT: bearish divergence detected AND close < EMA(trend_ema)
  SELL:  MACD histogram crosses below 0 while LONG
  COVER: MACD histogram crosses above 0 while SHORT

config.json strategy options:
  trend_ema       : trend filter EMA period (default 50)
  macd_fast       : MACD fast period (default 12)
  macd_slow       : MACD slow period (default 26)
  macd_signal     : MACD signal period (default 9)
  divergence_order: swing detection window — bars on each side (default 5)
  divergence_lookback: max bars between swing points to compare (default 30)
  volume_period   : rolling volume average period (default 20)
  volume_spike    : min vol / avg_vol for entry (default 0, 0 = disabled)
"""

from __future__ import annotations

import pandas as pd

from indicators.momentum import macd, rsi as compute_rsi
from indicators.trend import ema
from indicators.divergence import bullish_divergence, bearish_divergence
from strategies.directional import DirectionalStrategy


class MacdDivergenceStrategy(DirectionalStrategy):
    NAME = "macd_divergence"

    def __init__(self, config: dict):
        super().__init__(config)
        strat = config["strategy"]
        self.trend_ema_period = int(strat.get("trend_ema", 50))
        self.macd_fast = int(strat.get("macd_fast", 12))
        self.macd_slow = int(strat.get("macd_slow", 26))
        self.macd_signal_period = int(strat.get("macd_signal", 9))
        self.div_order = int(strat.get("divergence_order", 5))
        self.div_lookback = int(strat.get("divergence_lookback", 30))
        self.vol_period = int(strat.get("volume_period", 20))
        self.vol_spike = float(strat.get("volume_spike", 0))

    def required_history_bars(self) -> int:
        return max(self.macd_slow, self.trend_ema_period, self.vol_period) + self.div_lookback + 10

    def prepare_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        prepared = df.copy()
        m = macd(prepared["close"], self.macd_fast, self.macd_slow, self.macd_signal_period)
        prepared["macd_line"] = m.macd
        prepared["macd_signal"] = m.signal
        prepared["macd_hist"] = m.histogram
        prepared["trend_ema"] = ema(prepared["close"], self.trend_ema_period)

        # Pre-compute divergence flags
        prepared["bull_div"] = bullish_divergence(
            prepared["close"], m.histogram, order=self.div_order, lookback=self.div_lookback
        )
        prepared["bear_div"] = bearish_divergence(
            prepared["close"], m.histogram, order=self.div_order, lookback=self.div_lookback
        )

        if self.vol_spike > 0:
            prepared["vol_avg"] = prepared["volume"].rolling(self.vol_period, min_periods=1).mean()

        return prepared

    def signal_from_prepared(self, df: pd.DataFrame, index: int, direction: str):
        if index < 1:
            return None

        close = float(df["close"].iloc[index])
        trend = float(df["trend_ema"].iloc[index])
        hist = float(df["macd_hist"].iloc[index])
        prev_hist = float(df["macd_hist"].iloc[index - 1])

        # Exits
        if direction == "LONG":
            # MACD histogram crosses below zero
            if hist < 0 and prev_hist >= 0:
                return "SELL"
            return None

        if direction == "SHORT":
            # MACD histogram crosses above zero
            if hist > 0 and prev_hist <= 0:
                return "COVER"
            return None

        # Flat — check for divergence entries
        # Look at current bar and recent bars for divergence flag
        # (divergence is confirmed with a delay of `order` bars)
        bull_div = bool(df["bull_div"].iloc[index])
        bear_div = bool(df["bear_div"].iloc[index])

        # Volume gate
        vol_ok = True
        if self.vol_spike > 0 and "vol_avg" in df.columns:
            vol_ok = float(df["volume"].iloc[index]) >= float(df["vol_avg"].iloc[index]) * self.vol_spike

        if bull_div and close > trend and vol_ok:
            return "BUY"
        if bear_div and close < trend and vol_ok:
            return "SHORT"
        return None

    def describe_bar(self, df: pd.DataFrame, index: int) -> str:
        close = float(df["close"].iloc[index])
        hist = float(df["macd_hist"].iloc[index])
        trend = float(df["trend_ema"].iloc[index])
        bull = "BULL_DIV" if bool(df["bull_div"].iloc[index]) else ""
        bear = "BEAR_DIV" if bool(df["bear_div"].iloc[index]) else ""
        div_flag = bull or bear or "no_div"
        regime = "BULL" if close > trend else "BEAR"
        return f"close=₹{close:.2f} MACD_H={hist:.4f} EMA{self.trend_ema_period}={trend:.2f} {regime} {div_flag}"
