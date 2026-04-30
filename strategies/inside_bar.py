"""
strategies/inside_bar.py — Inside Bar Breakout Strategy
========================================================
Detects inside bars (NR pattern) and enters on breakout of the
mother bar's range with optional volume and RSI confirmation.

  BUY:   close > mother_bar_high AND (vol >= vol_spike × avg_vol) AND (RSI > 50 if enabled)
  SHORT: close < mother_bar_low AND (vol >= vol_spike × avg_vol) AND (RSI < 50 if enabled)
  SELL:  close < mother_bar_low (failed breakout) while LONG
  COVER: close > mother_bar_high (failed breakdown) while SHORT

config.json strategy options:
  volume_period   : rolling volume average period (default 20)
  volume_spike    : min vol / avg_vol ratio for confirmation (default 1.5, 0 = off)
  rsi_filter      : use RSI confirmation (default true)
  rsi_period      : RSI period (default 14)
  nr_lookback     : NR lookback for narrowest range detection (default 4, 0 = any inside bar)
"""

from __future__ import annotations

import pandas as pd

from indicators.patterns import inside_bar, nr7
from indicators.momentum import rsi as compute_rsi
from strategies.directional import DirectionalStrategy


class InsideBarStrategy(DirectionalStrategy):
    NAME = "inside_bar"

    def __init__(self, config: dict):
        super().__init__(config)
        strat = config["strategy"]
        self.vol_period = int(strat.get("volume_period", 20))
        self.vol_spike = float(strat.get("volume_spike", 1.5))
        self.rsi_filter = bool(strat.get("rsi_filter", True))
        self.rsi_period = int(strat.get("rsi_period", 14))
        self.nr_lookback = int(strat.get("nr_lookback", 4))

    def required_history_bars(self) -> int:
        return max(self.vol_period, self.rsi_period, self.nr_lookback) + 5

    def prepare_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        prepared = df.copy()
        prepared["inside"] = inside_bar(prepared["high"], prepared["low"])
        if self.nr_lookback > 0:
            prepared["nr"] = nr7(prepared["high"], prepared["low"], self.nr_lookback)
        # Mother bar (previous bar) range
        prepared["mother_high"] = prepared["high"].shift(1)
        prepared["mother_low"] = prepared["low"].shift(1)
        if self.vol_spike > 0:
            prepared["vol_avg"] = prepared["volume"].rolling(self.vol_period, min_periods=1).mean()
        if self.rsi_filter:
            prepared["rsi"] = compute_rsi(prepared["close"], self.rsi_period)
        return prepared

    def signal_from_prepared(self, df: pd.DataFrame, index: int, direction: str):
        if index < 2:
            return None

        # Check if PREVIOUS bar was an inside bar (signal forms, breakout on current bar)
        was_inside = bool(df["inside"].iloc[index - 1])
        if self.nr_lookback > 0 and "nr" in df.columns:
            was_inside = was_inside or bool(df["nr"].iloc[index - 1])

        if not was_inside and direction == "FLAT":
            return None

        close = float(df["close"].iloc[index])
        mother_high = float(df["mother_high"].iloc[index])
        mother_low = float(df["mother_low"].iloc[index])

        if direction == "LONG":
            # Failed breakout: price returned below mother low
            if close < mother_low:
                return "SELL"
            return None

        if direction == "SHORT":
            # Failed breakdown: price returned above mother high
            if close > mother_high:
                return "COVER"
            return None

        # Flat — look for breakout of inside bar range
        # Volume confirmation
        vol_ok = True
        if self.vol_spike > 0 and "vol_avg" in df.columns:
            vol_ok = float(df["volume"].iloc[index]) >= float(df["vol_avg"].iloc[index]) * self.vol_spike

        # RSI confirmation
        rsi_ok_long = True
        rsi_ok_short = True
        if self.rsi_filter and "rsi" in df.columns:
            rsi_val = float(df["rsi"].iloc[index])
            rsi_ok_long = rsi_val > 50
            rsi_ok_short = rsi_val < 50

        if close > mother_high and vol_ok and rsi_ok_long:
            return "BUY"
        if close < mother_low and vol_ok and rsi_ok_short:
            return "SHORT"
        return None

    def describe_bar(self, df: pd.DataFrame, index: int) -> str:
        close = float(df["close"].iloc[index])
        inside_flag = "IB" if bool(df["inside"].iloc[index]) else ""
        m_high = float(df["mother_high"].iloc[index]) if pd.notna(df["mother_high"].iloc[index]) else 0
        m_low = float(df["mother_low"].iloc[index]) if pd.notna(df["mother_low"].iloc[index]) else 0
        rsi_val = f" RSI={float(df['rsi'].iloc[index]):.1f}" if "rsi" in df.columns else ""
        return f"close=₹{close:.2f} MH={m_high:.2f} ML={m_low:.2f} {inside_flag}{rsi_val}"
