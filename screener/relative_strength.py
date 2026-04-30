"""
screener/relative_strength.py — Relative Strength Screener
============================================================
Ranks stocks by their performance relative to the Nifty 50 index.
Stocks outperforming the index get higher scores (Minervini's RS line).

Scoring:
  RS = (stock_return_3m / nifty_return_3m) × 100
  Combined with momentum and volume for the final score.

Config keys (via config.json screener section):
  rs_lookback_days : days for RS calculation (default 63 = ~3 months)
  rs_min_percentile: min RS percentile to pass filter (default 70)
"""

from __future__ import annotations

import pandas as pd
import numpy as np

from screener.base import BaseScreener


class RelativeStrengthScreener(BaseScreener):
    """Screens for stocks with high relative strength vs. Nifty 50."""

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.rs_lookback = int(self.scr_cfg.get("rs_lookback_days", 63))
        self.rs_min_pctile = float(self.scr_cfg.get("rs_min_percentile", 70))

    def extra_metrics(self, hist: pd.DataFrame) -> dict:
        if len(hist) < 2:
            return {}

        # Calculate returns over lookback period
        lookback = min(self.rs_lookback, len(hist) - 1)
        current_close = float(hist["close"].iloc[-1])
        past_close = float(hist["close"].iloc[-lookback])

        if past_close <= 0:
            return {}

        stock_return = (current_close - past_close) / past_close * 100

        # 1-month return (for multi-period weighting)
        month_bars = min(21, len(hist) - 1)
        month_close = float(hist["close"].iloc[-month_bars])
        stock_return_1m = (current_close - month_close) / month_close * 100 if month_close > 0 else 0

        # 1-week return
        week_bars = min(5, len(hist) - 1)
        week_close = float(hist["close"].iloc[-week_bars])
        stock_return_1w = (current_close - week_close) / week_close * 100 if week_close > 0 else 0

        # Distance from 52-week high (approx 250 bars)
        high_bars = min(250, len(hist))
        high_252 = float(hist["high"].tail(high_bars).max())
        pct_from_high = (current_close - high_252) / high_252 * 100 if high_252 > 0 else -100

        return {
            "rs_return_3m": round(stock_return, 2),
            "rs_return_1m": round(stock_return_1m, 2),
            "rs_return_1w": round(stock_return_1w, 2),
            "pct_from_52w_high": round(pct_from_high, 2),
        }

    def passes_filter(self, metrics: dict) -> bool:
        # Must have positive 3-month return
        rs_3m = metrics.get("rs_return_3m", 0)
        if rs_3m <= 0:
            return False

        # Not too far from 52-week high (within 25%)
        pct_high = metrics.get("pct_from_52w_high", -100)
        if pct_high < -25:
            return False

        return True

    def score(self, metrics: dict) -> float:
        # Weighted RS score: 3m×0.4 + 1m×0.4 + 1w×0.2
        rs_3m = metrics.get("rs_return_3m", 0)
        rs_1m = metrics.get("rs_return_1m", 0)
        rs_1w = metrics.get("rs_return_1w", 0)

        # Bonus for being near 52-week high (momentum)
        pct_high = metrics.get("pct_from_52w_high", -100)
        near_high_bonus = max(0, 10 + pct_high)  # 0 if >10% below, up to 10 if at high

        # Volume spike bonus
        vol_spike = metrics.get("vol_spike", 1.0)
        vol_bonus = max(0, (vol_spike - 1) * 10)

        return rs_3m * 0.4 + rs_1m * 0.4 + rs_1w * 0.2 + near_high_bonus * 0.5 + vol_bonus
