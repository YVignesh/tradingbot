"""
screener/gap_momentum.py — Gap Momentum screener
=================================================
Selects stocks that gapped significantly at open and maintained the gap
direction through close — a sign of strong conviction move, often driven
by news, earnings, or institutional flow.

  gap_pct = (open - prev_close) / prev_close * 100
  close_ratio = fraction of gap maintained at close
                (close - prev_close) / (open - prev_close)

A close_ratio > 0.5 means price held more than half the gap — follow-through.

config.json screener options:
  gap_min_pct       : minimum absolute gap % to qualify (default 1.5)
  gap_close_ratio   : minimum fraction of gap held at close (default 0.5)
  gap_direction     : "up", "down", or "both" (default "both")
  gap_vol_min       : minimum volume spike to confirm (default 1.5)
"""

from __future__ import annotations

import pandas as pd

from screener.base import BaseScreener


class GapMomentumScreener(BaseScreener):
    """Selects stocks with strong gap follow-through — catalyst-driven moves."""

    def extra_metrics(self, hist: pd.DataFrame) -> dict:
        if len(hist) < 3:
            return {}

        prev_close = float(hist["close"].iloc[-2])
        today_open = float(hist["open"].iloc[-1])
        today_close = float(hist["close"].iloc[-1])

        if prev_close <= 0:
            return {}

        gap_pct = (today_open - prev_close) / prev_close * 100
        gap_size = today_open - prev_close

        if abs(gap_size) < 0.001:
            close_ratio = 0.0
        else:
            close_ratio = (today_close - prev_close) / gap_size

        return {
            "gap_pct_today": round(gap_pct, 2),
            "close_ratio": round(close_ratio, 3),
        }

    def passes_filter(self, metrics: dict) -> bool:
        min_gap = float(self.scr_cfg.get("gap_min_pct", 1.5))
        min_close_ratio = float(self.scr_cfg.get("gap_close_ratio", 0.5))
        direction = str(self.scr_cfg.get("gap_direction", "both")).lower()
        min_vol = float(self.scr_cfg.get("gap_vol_min", 1.5))

        gap = float(metrics.get("gap_pct_today", 0.0))
        close_ratio = float(metrics.get("close_ratio", 0.0))
        vol_spike = float(metrics.get("volume_spike", 0.0))

        if abs(gap) < min_gap:
            return False
        if direction == "up" and gap < 0:
            return False
        if direction == "down" and gap > 0:
            return False
        if close_ratio < min_close_ratio:
            return False
        if vol_spike < min_vol:
            return False
        return True

    def score(self, metrics: dict) -> float:
        gap = float(metrics.get("gap_pct_today", 0.0))
        close_ratio = float(metrics.get("close_ratio", 0.0))
        vol_spike = float(metrics.get("volume_spike", 1.0))
        return abs(gap) * close_ratio * 10.0 + vol_spike * 5.0
