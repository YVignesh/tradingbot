"""
screener/breakout.py — Breakout screener strategy
==================================================
Selects stocks consolidating near recent highs with expanding volume.
Extra metrics: distance from 20-day high, 5d/20d volume expansion ratio.

config.json screener options:
  pct_near_high      : max % below 20d high to qualify (default 3.0)
  vol_expansion_min  : min 5d/20d volume ratio to qualify (default 1.2)
"""

from __future__ import annotations

import pandas as pd

from screener.base import BaseScreener


class BreakoutScreener(BaseScreener):
    """Picks stocks within striking distance of 20d highs with volume build-up."""

    def extra_metrics(self, hist: pd.DataFrame) -> dict:
        if len(hist) < 20:
            return {}

        high_20d = float(hist["high"].rolling(20, min_periods=20).max().iloc[-1])
        close = float(hist["close"].iloc[-1])
        pct_from_high = ((close - high_20d) / high_20d) * 100 if high_20d > 0 else 0.0

        avg_vol_20 = float(hist["volume"].rolling(20, min_periods=20).mean().iloc[-1])
        avg_vol_5 = float(hist["volume"].rolling(5, min_periods=5).mean().iloc[-1])
        vol_expansion = (avg_vol_5 / avg_vol_20) if avg_vol_20 > 0 else 1.0

        return {
            "high_20d": round(high_20d, 2),
            "pct_from_high": round(pct_from_high, 2),
            "vol_expansion": round(vol_expansion, 4),
        }

    def passes_filter(self, metrics: dict) -> bool:
        pct_near_high = float(self.scr_cfg.get("pct_near_high", 3.0))
        vol_expansion_min = float(self.scr_cfg.get("vol_expansion_min", 1.2))

        pct_from_high = float(metrics.get("pct_from_high", -100.0))
        vol_expansion = float(metrics.get("vol_expansion", 0.0))

        # Price must be within pct_near_high% below the 20d high
        if pct_from_high < -pct_near_high:
            return False
        # Volume must be expanding
        if vol_expansion < vol_expansion_min:
            return False
        return True

    def score(self, metrics: dict) -> float:
        pct_from_high = float(metrics.get("pct_from_high", -100.0))
        vol_expansion = float(metrics.get("vol_expansion", 1.0))
        gap_pct = float(metrics.get("gap_pct", 0.0))
        # Closer to high = higher score; more volume expansion = higher score
        return (
            -pct_from_high * 2.0
            + vol_expansion * 15.0
            - gap_pct * 0.5
        )
