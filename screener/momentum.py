"""
screener/momentum.py — Momentum screener strategy
==================================================
Ranks symbols by 5-day momentum, volume surge, and low gap.
Formula: momentum_5d × 0.6 + volume_spike × 25 − gap_pct × 0.5
"""

from __future__ import annotations

import pandas as pd

from screener.base import BaseScreener


class MomentumScreener(BaseScreener):
    """Default screener — selects stocks with strong recent momentum and high volume."""

    def extra_metrics(self, hist: pd.DataFrame) -> dict:
        return {}

    def passes_filter(self, metrics: dict) -> bool:
        return True

    def score(self, metrics: dict) -> float:
        # Rebalanced weights: momentum and volume roughly equal contribution (#11)
        return (
            float(metrics.get("momentum_5d", 0.0)) * 2.0
            + float(metrics.get("volume_spike", 0.0)) * 5.0
            - float(metrics.get("gap_pct", 0.0)) * 1.0
        )
