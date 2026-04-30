"""
strategies/gap_and_go.py — Gap & Go / Gap Fill Strategy
========================================================
Trades morning gaps based on whether they hold or fill.

Gap & Go (continuation): if gap > threshold and price holds above
  yesterday's high for the first N bars → BUY.
Gap Fill (reversal): if gap > threshold but price falls back inside
  yesterday's range → SHORT the gap fill.

config.json strategy options:
  gap_pct_min       : minimum gap % to qualify (default 1.5)
  hold_bars         : bars to confirm gap hold at open (default 3)
  rsi_period        : RSI period (default 14)
  rsi_filter        : require RSI confirmation (default true)
  volume_period     : rolling volume avg period (default 20)
  volume_spike      : min vol / avg_vol (default 1.5, 0 = off)
  gap_fill_mode     : trade gap fills as well (default false)
"""

from __future__ import annotations

import pandas as pd

from indicators.momentum import rsi as compute_rsi
from indicators.trend import ema
from strategies.directional import DirectionalStrategy


class GapAndGoStrategy(DirectionalStrategy):
    NAME = "gap_and_go"

    def __init__(self, config: dict):
        super().__init__(config)
        strat = config["strategy"]
        self.gap_pct_min = float(strat.get("gap_pct_min", 1.5))
        self.hold_bars = int(strat.get("hold_bars", 3))
        self.rsi_period = int(strat.get("rsi_period", 14))
        self.rsi_filter = bool(strat.get("rsi_filter", True))
        self.vol_period = int(strat.get("volume_period", 20))
        self.vol_spike = float(strat.get("volume_spike", 1.5))
        self.gap_fill_mode = bool(strat.get("gap_fill_mode", False))

    def required_history_bars(self) -> int:
        return max(self.vol_period, self.rsi_period, 50) + 5

    def prepare_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        prepared = df.copy()

        # Detect daily boundaries and compute gap
        prepared["date"] = prepared.index.date
        prepared["bar_of_day"] = prepared.groupby("date").cumcount()

        # Previous day's close/high/low
        daily_close = prepared.groupby("date")["close"].last().shift(1)
        daily_high = prepared.groupby("date")["high"].max().shift(1)
        daily_low = prepared.groupby("date")["low"].min().shift(1)
        prepared["prev_day_close"] = prepared["date"].map(daily_close)
        prepared["prev_day_high"] = prepared["date"].map(daily_high)
        prepared["prev_day_low"] = prepared["date"].map(daily_low)

        # Gap %
        prepared["gap_pct"] = (
            (prepared["open"] - prepared["prev_day_close"]) / prepared["prev_day_close"] * 100
        ).where(prepared["bar_of_day"] == 0)
        prepared["gap_pct"] = prepared.groupby("date")["gap_pct"].transform("first")

        # Day's opening price (first bar open of each day)
        day_open = prepared.groupby("date")["open"].first()
        prepared["day_open"] = prepared["date"].map(day_open)

        # Running high of the day
        prepared["day_high_so_far"] = prepared.groupby("date")["high"].cummax()
        prepared["day_low_so_far"] = prepared.groupby("date")["low"].cummin()

        if self.rsi_filter:
            prepared["rsi"] = compute_rsi(prepared["close"], self.rsi_period)
        if self.vol_spike > 0:
            prepared["vol_avg"] = prepared["volume"].rolling(self.vol_period, min_periods=1).mean()

        return prepared

    def signal_from_prepared(self, df: pd.DataFrame, index: int, direction: str):
        if index < 1:
            return None

        bar_of_day = int(df["bar_of_day"].iloc[index])
        gap_pct = df["gap_pct"].iloc[index]
        if pd.isna(gap_pct):
            return None

        gap_pct = float(gap_pct)
        close = float(df["close"].iloc[index])
        prev_day_high = df["prev_day_high"].iloc[index]
        prev_day_low = df["prev_day_low"].iloc[index]
        prev_day_close = df["prev_day_close"].iloc[index]

        if pd.isna(prev_day_high) or pd.isna(prev_day_close):
            return None

        prev_day_high = float(prev_day_high)
        prev_day_low = float(prev_day_low)
        prev_day_close = float(prev_day_close)
        day_low_so_far = float(df["day_low_so_far"].iloc[index])
        day_high_so_far = float(df["day_high_so_far"].iloc[index])

        has_gap_up = gap_pct >= self.gap_pct_min
        has_gap_down = gap_pct <= -self.gap_pct_min

        # Exits
        if direction == "LONG":
            # Exit if price falls back below previous day's high (gap failed)
            if close < prev_day_high:
                return "SELL"
            return None

        if direction == "SHORT":
            # Exit if price rises above previous day's close (gap fill failed)
            if close > prev_day_close:
                return "COVER"
            return None

        # Only enter during first portion of the day (after hold_bars confirm)
        if bar_of_day < self.hold_bars or bar_of_day > 25:
            return None

        # Volume gate
        vol_ok = True
        if self.vol_spike > 0 and "vol_avg" in df.columns:
            vol_ok = float(df["volume"].iloc[index]) >= float(df["vol_avg"].iloc[index]) * self.vol_spike

        # RSI gate
        rsi_ok_long = True
        rsi_ok_short = True
        if self.rsi_filter and "rsi" in df.columns:
            rsi_val = float(df["rsi"].iloc[index])
            rsi_ok_long = rsi_val > 50
            rsi_ok_short = rsi_val < 50

        # Gap & Go: gap up + price held above prev day high for hold_bars
        if has_gap_up and bar_of_day == self.hold_bars:
            if day_low_so_far > prev_day_high and vol_ok and rsi_ok_long:
                return "BUY"
            # Gap Fill mode: gap up but price came back into range
            if self.gap_fill_mode and close < prev_day_close and vol_ok and rsi_ok_short:
                return "SHORT"

        # Gap down & go short
        if has_gap_down and bar_of_day == self.hold_bars:
            if day_high_so_far < prev_day_low and vol_ok and rsi_ok_short:
                return "SHORT"
            # Gap Fill mode: gap down but price recovered
            if self.gap_fill_mode and close > prev_day_close and vol_ok and rsi_ok_long:
                return "BUY"

        return None

    def describe_bar(self, df: pd.DataFrame, index: int) -> str:
        close = float(df["close"].iloc[index])
        gap = float(df["gap_pct"].iloc[index]) if pd.notna(df["gap_pct"].iloc[index]) else 0
        bar_num = int(df["bar_of_day"].iloc[index])
        rsi_val = f" RSI={float(df['rsi'].iloc[index]):.1f}" if "rsi" in df.columns else ""
        return f"close=₹{close:.2f} gap={gap:+.2f}% bar#{bar_num}{rsi_val}"
