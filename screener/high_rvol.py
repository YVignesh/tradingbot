"""
screener/high_rvol.py — High Relative Volume screener
======================================================
Selects stocks with unusually high volume relative to their historical
average — a signal of institutional interest, news, or catalyst activity.
High RVOL stocks tend to have large intraday ranges and momentum follow-through.

config.json screener options:
  rvol_min        : minimum relative volume ratio to qualify (default 2.0)
  rvol_lookback   : baseline volume lookback in days (default 20)
  rvol_mom_min    : minimum 5-day momentum % to pair with RVOL (default 0.0)
"""

from __future__ import annotations

import pandas as pd

from screener.base import BaseScreener


class HighRvolScreener(BaseScreener):
    """Selects stocks with surging volume — signs of institutional conviction."""

    def extra_metrics(self, hist: pd.DataFrame) -> dict:
        if len(hist) < 5:
            return {}

        volume = hist["volume"]
        lookback = int(self.scr_cfg.get("rvol_lookback", 20))

        avg_vol_n = float(volume.tail(lookback).mean())
        avg_vol_5 = float(volume.tail(5).mean())
        today_vol = float(volume.iloc[-1])

        rvol_today = today_vol / avg_vol_n if avg_vol_n > 0 else 0.0
        rvol_5d = avg_vol_5 / avg_vol_n if avg_vol_n > 0 else 0.0

        # Price range on high-volume day
        last_range_pct = float(
            (hist["high"].iloc[-1] - hist["low"].iloc[-1]) / hist["close"].iloc[-1] * 100
        )

        return {
            "rvol_today": round(rvol_today, 3),
            "rvol_5d": round(rvol_5d, 3),
            "today_range_pct": round(last_range_pct, 2),
        }

    def passes_filter(self, metrics: dict) -> bool:
        min_rvol = float(self.scr_cfg.get("rvol_min", 2.0))
        min_mom = float(self.scr_cfg.get("rvol_mom_min", 0.0))

        # Use either single-day or 5-day RVOL as the qualifying metric
        rvol = max(
            float(metrics.get("rvol_today", 0.0)),
            float(metrics.get("rvol_5d", 0.0)),
        )
        if rvol < min_rvol:
            return False
        if float(metrics.get("momentum_5d", 0.0)) < min_mom:
            return False
        return True

    def score(self, metrics: dict) -> float:
        rvol = max(
            float(metrics.get("rvol_today", 0.0)),
            float(metrics.get("rvol_5d", 0.0)),
        )
        mom = float(metrics.get("momentum_5d", 0.0))
        range_pct = float(metrics.get("today_range_pct", 0.0))
        return rvol * 20.0 + mom * 0.4 + range_pct * 2.0
