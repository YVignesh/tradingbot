"""
screener/vcp.py — Volatility Contraction Pattern (VCP) screener
================================================================
Identifies stocks in an uptrend where volatility (ATR and range) is
contracting and volume is drying up — classic Minervini setup before a
high-probability breakout.

Scoring: lower volatility contraction = higher score (tighter = better)

config.json screener options:
  vcp_trend_sma       : SMA period for uptrend filter (default 50)
  vcp_vol_contraction : max ratio of current 10d ATR to 30d ATR (default 0.7)
  vcp_vol_dryup       : max ratio of 5d avg volume to 20d avg volume (default 0.8)
  vcp_range_contraction: max ratio of 10d range to 30d range (default 0.65)
"""

from __future__ import annotations

import pandas as pd

from screener.base import BaseScreener


class VCPScreener(BaseScreener):
    """Selects stocks exhibiting Volatility Contraction Patterns before breakout."""

    def extra_metrics(self, hist: pd.DataFrame) -> dict:
        if len(hist) < 30:
            return {}

        close = hist["close"]
        high = hist["high"]
        low = hist["low"]
        volume = hist["volume"]

        # Trend: price above SMA50
        sma_period = int(self.scr_cfg.get("vcp_trend_sma", 50))
        sma = close.rolling(min(sma_period, len(hist)), min_periods=10).mean()
        above_sma = float(close.iloc[-1]) > float(sma.iloc[-1]) if not sma.isna().iloc[-1] else False

        # ATR-based volatility contraction
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)

        atr_10 = float(tr.tail(10).mean())
        atr_30 = float(tr.tail(30).mean())
        vol_contraction = atr_10 / atr_30 if atr_30 > 0 else 1.0

        # Range contraction: recent range vs older range
        range_10 = float(high.tail(10).max() - low.tail(10).min())
        range_30 = float(high.tail(30).max() - low.tail(30).min())
        range_contraction = range_10 / range_30 if range_30 > 0 else 1.0

        # Volume dry-up: recent volume vs baseline
        vol_5d = float(volume.tail(5).mean())
        vol_20d = float(volume.tail(20).mean())
        vol_dryup = vol_5d / vol_20d if vol_20d > 0 else 1.0

        return {
            "above_sma": above_sma,
            "vol_contraction": round(vol_contraction, 4),
            "range_contraction": round(range_contraction, 4),
            "vol_dryup": round(vol_dryup, 4),
        }

    def passes_filter(self, metrics: dict) -> bool:
        if not metrics.get("above_sma", False):
            return False

        max_vol_contraction = float(self.scr_cfg.get("vcp_vol_contraction", 0.75))
        max_vol_dryup = float(self.scr_cfg.get("vcp_vol_dryup", 0.85))
        max_range_contraction = float(self.scr_cfg.get("vcp_range_contraction", 0.70))

        if float(metrics.get("vol_contraction", 1.0)) > max_vol_contraction:
            return False
        if float(metrics.get("vol_dryup", 1.0)) > max_vol_dryup:
            return False
        if float(metrics.get("range_contraction", 1.0)) > max_range_contraction:
            return False
        return True

    def score(self, metrics: dict) -> float:
        # Tighter contraction = higher score
        vol_c = float(metrics.get("vol_contraction", 1.0))
        range_c = float(metrics.get("range_contraction", 1.0))
        vol_d = float(metrics.get("vol_dryup", 1.0))
        mom = float(metrics.get("momentum_5d", 0.0))
        return (
            -vol_c * 40.0
            - range_c * 30.0
            - vol_d * 20.0
            + mom * 0.5
        )
