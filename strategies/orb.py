"""
strategies/orb.py — Opening Range Breakout strategy
====================================================
Waits for the first N bars of each trading session to establish the
Opening Range (OR), then trades breakouts above the OR high (BUY) or
below the OR low (SHORT). A failed breakout reversal exits the trade.

Requires a DatetimeIndex (intraday candles). Works in both live and backtest.

config.json strategy options:
  orb_bars      : number of opening bars to form the range (default 3)
  orb_rsi_filter: if true, require RSI > 50 for longs / < 50 for shorts (default false)
  rsi_period    : RSI period used when orb_rsi_filter is enabled (default 14)
"""

from __future__ import annotations

import pandas as pd

from indicators.momentum import rsi as compute_rsi
from strategies.directional import DirectionalStrategy


class OrbStrategy(DirectionalStrategy):
    NAME = "orb"

    def __init__(self, config: dict):
        super().__init__(config)
        strat = config["strategy"]
        self.orb_bars = int(strat.get("orb_bars", 3))
        self.rsi_filter = bool(strat.get("orb_rsi_filter", False))
        self.rsi_period = int(strat.get("rsi_period", 14))

    def required_history_bars(self) -> int:
        return max(self.rsi_period + 5, self.orb_bars + 2)

    def prepare_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        prepared = df.copy()

        orb_high = pd.Series(float("nan"), index=prepared.index)
        orb_low = pd.Series(float("nan"), index=prepared.index)
        bar_of_day = pd.Series(0, index=prepared.index)

        if isinstance(prepared.index, pd.DatetimeIndex):
            dates = pd.Series(prepared.index.date, index=prepared.index)
            for date in pd.unique(dates):
                mask = dates == date
                day_df = prepared[mask]
                for i, idx in enumerate(day_df.index):
                    bar_of_day[idx] = i
                    if i >= self.orb_bars:
                        orb_high[idx] = float(day_df["high"].iloc[: self.orb_bars].max())
                        orb_low[idx] = float(day_df["low"].iloc[: self.orb_bars].min())
        else:
            # Fallback: treat full series as single day
            or_h = prepared["high"].iloc[: self.orb_bars].max()
            or_l = prepared["low"].iloc[: self.orb_bars].min()
            for i in range(self.orb_bars, len(prepared)):
                idx = prepared.index[i]
                orb_high[idx] = or_h
                orb_low[idx] = or_l
                bar_of_day[idx] = i

        prepared["orb_high"] = orb_high
        prepared["orb_low"] = orb_low
        prepared["bar_of_day"] = bar_of_day

        if self.rsi_filter:
            prepared["rsi"] = compute_rsi(prepared["close"], self.rsi_period)

        return prepared

    def signal_from_prepared(self, df: pd.DataFrame, index: int, direction: str):
        bar = int(df["bar_of_day"].iloc[index])
        if bar < self.orb_bars:
            return None

        orb_h = df["orb_high"].iloc[index]
        orb_l = df["orb_low"].iloc[index]
        if pd.isna(orb_h) or pd.isna(orb_l):
            return None

        close = float(df["close"].iloc[index])
        orb_h = float(orb_h)
        orb_l = float(orb_l)

        if self.rsi_filter:
            rsi_val = float(df["rsi"].iloc[index]) if "rsi" in df.columns else 50.0
        else:
            rsi_val = 50.0

        if direction == "FLAT":
            if close > orb_h and (not self.rsi_filter or rsi_val > 50):
                return "BUY"
            if close < orb_l and (not self.rsi_filter or rsi_val < 50):
                return "SHORT"
        elif direction == "LONG":
            if close < orb_l:  # Failed breakout — price fell below range
                return "SELL"
        elif direction == "SHORT":
            if close > orb_h:  # Failed breakdown — price rose above range
                return "COVER"
        return None

    def describe_bar(self, df: pd.DataFrame, index: int) -> str:
        close = float(df["close"].iloc[index])
        orb_h = df["orb_high"].iloc[index]
        orb_l = df["orb_low"].iloc[index]
        bar = int(df["bar_of_day"].iloc[index])
        if pd.isna(orb_h):
            return f"close=₹{close:.2f} ORB=forming bar#{bar}"
        return (
            f"close=₹{close:.2f} ORB_H={float(orb_h):.2f} "
            f"ORB_L={float(orb_l):.2f} bar#{bar}"
        )
