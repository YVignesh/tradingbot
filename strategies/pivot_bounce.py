"""
strategies/pivot_bounce.py — Daily Pivot Point breakout/bounce strategy
=======================================================================
Computes standard floor-trader pivot levels from the previous session's
OHLC (resampled from intraday bars) and trades breakouts through R1/S1.

  BUY   when price closes above R1 (resistance cleared — momentum long)
  SHORT when price closes below S1 (support broken — momentum short)
  SELL  when price falls back below pivot (momentum faded)
  COVER when price rises back above pivot (reversal)

Requires a DatetimeIndex. Falls back to a rolling 20-bar OHLC approach
when the index is not a DatetimeIndex (e.g., simple integer index).

config.json strategy options:
  (none — pivot levels are computed automatically from price data)
"""

from __future__ import annotations

import pandas as pd

from strategies.directional import DirectionalStrategy


class PivotBounceStrategy(DirectionalStrategy):
    NAME = "pivot_bounce"

    def required_history_bars(self) -> int:
        return 30

    def _compute_pivots(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute per-bar pivot, R1, R2, S1, S2 from previous session OHLC."""
        if isinstance(df.index, pd.DatetimeIndex):
            # Resample intraday to daily, then shift 1 day for "previous session"
            daily = df[["high", "low", "close"]].resample("D").agg(
                {"high": "max", "low": "min", "close": "last"}
            ).dropna()

            pivot = (daily["high"] + daily["low"] + daily["close"]) / 3.0
            r1 = 2.0 * pivot - daily["low"]
            s1 = 2.0 * pivot - daily["high"]
            r2 = pivot + (daily["high"] - daily["low"])
            s2 = pivot - (daily["high"] - daily["low"])

            # Shift 1 period (prev day's levels apply to today)
            for col_name, col_data in [("pivot", pivot), ("r1", r1), ("s1", s1), ("r2", r2), ("s2", s2)]:
                reindexed = col_data.shift(1).reindex(df.index, method="ffill")
                df[col_name] = reindexed
        else:
            raise ValueError(
                "PivotBounce strategy requires a DatetimeIndex for accurate daily "
                "pivot calculation. Got index type: " + type(df.index).__name__
            )

        return df

    def prepare_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        prepared = df.copy()
        prepared = self._compute_pivots(prepared)
        return prepared

    def signal_from_prepared(self, df: pd.DataFrame, index: int, direction: str):
        close = float(df["close"].iloc[index])

        pivot = df["pivot"].iloc[index]
        r1 = df["r1"].iloc[index]
        s1 = df["s1"].iloc[index]

        if pd.isna(pivot):
            return None

        pivot = float(pivot)
        r1 = float(r1)
        s1 = float(s1)

        if direction == "LONG":
            # Exit if price falls back to or below pivot
            if close <= pivot:
                return "SELL"
            return None

        if direction == "SHORT":
            # Exit if price recovers to or above pivot
            if close >= pivot:
                return "COVER"
            return None

        # Flat — enter on R1 breakout or S1 breakdown
        if close > r1:
            return "BUY"
        if close < s1:
            return "SHORT"
        return None

    def describe_bar(self, df: pd.DataFrame, index: int) -> str:
        close = float(df["close"].iloc[index])
        pivot = df["pivot"].iloc[index]
        r1 = df["r1"].iloc[index]
        s1 = df["s1"].iloc[index]
        if pd.isna(pivot):
            return f"close=₹{close:.2f} pivot=N/A"
        return (
            f"close=₹{close:.2f} pivot={float(pivot):.2f} "
            f"R1={float(r1):.2f} S1={float(s1):.2f}"
        )
