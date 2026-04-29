"""
screener/range_position.py — N-Day Range Position screener
==========================================================
Selects stocks trading in the upper portion of their N-day price range.
Stocks near multi-week/multi-month highs tend to have institutional accumulation
and are set up for continuation if volume confirms.

config.json screener options:
  range_days        : lookback window for the range (default 100)
  range_min_pct     : minimum position in range to qualify, 0–1 (default 0.70)
                      0.70 means stock must be in top 30% of its range
  range_vol_confirm : require volume_spike > 1.0 (default true)
"""

from __future__ import annotations

import pandas as pd

from screener.base import BaseScreener


class RangePositionScreener(BaseScreener):
    """Selects stocks near multi-month highs with volume confirmation."""

    def extra_metrics(self, hist: pd.DataFrame) -> dict:
        range_days = int(self.scr_cfg.get("range_days", 100))
        window = min(range_days, len(hist))
        if window < 10:
            return {}

        period_high = float(hist["high"].tail(window).max())
        period_low = float(hist["low"].tail(window).min())
        close = float(hist["close"].iloc[-1])

        price_range = period_high - period_low
        range_pct = (close - period_low) / price_range if price_range > 0 else 0.5

        # Distance from period high
        pct_from_high = (close - period_high) / period_high * 100 if period_high > 0 else 0.0

        return {
            "period_high": round(period_high, 2),
            "period_low": round(period_low, 2),
            "range_pct": round(range_pct, 4),
            "pct_from_period_high": round(pct_from_high, 2),
        }

    def passes_filter(self, metrics: dict) -> bool:
        min_range_pct = float(self.scr_cfg.get("range_min_pct", 0.70))
        vol_confirm = bool(self.scr_cfg.get("range_vol_confirm", True))

        if float(metrics.get("range_pct", 0.0)) < min_range_pct:
            return False
        if vol_confirm and float(metrics.get("volume_spike", 0.0)) < 1.0:
            return False
        return True

    def score(self, metrics: dict) -> float:
        range_pct = float(metrics.get("range_pct", 0.0))
        vol_spike = float(metrics.get("volume_spike", 1.0))
        mom = float(metrics.get("momentum_5d", 0.0))
        gap = float(metrics.get("gap_pct", 0.0))
        return (
            range_pct * 50.0
            + vol_spike * 10.0
            + mom * 0.3
            - gap * 0.5
        )
