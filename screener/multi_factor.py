"""
screener/multi_factor.py — Multi-Factor Composite screener
===========================================================
Combines five independent factors into a single score:
  1. Momentum    — 5-day price momentum
  2. Trend       — price position relative to SMA50
  3. Volume      — volume spike vs 20-day baseline
  4. Breakout    — proximity to 20-day high
  5. Quality     — low ATR% (smooth trend, not noisy)

Each factor is normalised to a 0–100 sub-score; weights are configurable.

config.json screener options:
  mf_trend_sma         : SMA period for trend factor (default 50)
  mf_weight_momentum   : weight for momentum factor (default 1.0)
  mf_weight_trend      : weight for trend factor (default 1.0)
  mf_weight_volume     : weight for volume factor (default 1.0)
  mf_weight_breakout   : weight for breakout proximity factor (default 1.0)
  mf_weight_quality    : weight for quality/smoothness factor (default 0.5)
  mf_min_score         : minimum composite score to qualify (default 40)
"""

from __future__ import annotations

import pandas as pd

from screener.base import BaseScreener


class MultiFactorScreener(BaseScreener):
    """Ranks symbols by a configurable multi-factor composite score."""

    def extra_metrics(self, hist: pd.DataFrame) -> dict:
        sma_period = int(self.scr_cfg.get("mf_trend_sma", 50))
        if len(hist) < max(sma_period, 20):
            return {}

        close = hist["close"]
        high = hist["high"]
        low = hist["low"]

        # Trend factor: % above/below SMA50
        sma = close.rolling(min(sma_period, len(hist)), min_periods=10).mean()
        trend_pct = float((close.iloc[-1] / sma.iloc[-1] - 1) * 100) if not sma.isna().iloc[-1] else 0.0

        # Breakout factor: % from 20d high (negative = below, 0 = at high)
        high_20d = float(high.tail(20).max())
        pct_from_high = float((close.iloc[-1] - high_20d) / high_20d * 100)

        # Quality factor: ATR% (lower = smoother trend)
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr_14 = float(tr.tail(14).mean())
        atr_pct = atr_14 / float(close.iloc[-1]) * 100 if float(close.iloc[-1]) > 0 else 5.0

        return {
            "trend_pct": round(trend_pct, 2),
            "pct_from_high_20d": round(pct_from_high, 2),
            "atr_pct": round(atr_pct, 3),
        }

    def passes_filter(self, metrics: dict) -> bool:
        min_score = float(self.scr_cfg.get("mf_min_score", 40.0))
        return self.score(metrics) >= min_score

    def score(self, metrics: dict) -> float:
        w_mom = float(self.scr_cfg.get("mf_weight_momentum", 1.0))
        w_trend = float(self.scr_cfg.get("mf_weight_trend", 1.0))
        w_vol = float(self.scr_cfg.get("mf_weight_volume", 1.0))
        w_breakout = float(self.scr_cfg.get("mf_weight_breakout", 1.0))
        w_quality = float(self.scr_cfg.get("mf_weight_quality", 0.5))

        # Momentum sub-score (0–100)
        mom = float(metrics.get("momentum_5d", 0.0))
        mom_score = min(100.0, max(0.0, mom * 10.0 + 50.0))

        # Trend sub-score (0–100)
        trend_pct = float(metrics.get("trend_pct", 0.0))
        trend_score = min(100.0, max(0.0, trend_pct * 5.0 + 50.0))

        # Volume sub-score (0–100)
        vol_spike = float(metrics.get("volume_spike", 1.0))
        vol_score = min(100.0, max(0.0, (vol_spike - 1.0) * 50.0 + 50.0))

        # Breakout sub-score (0–100): 0% from high = 100, -5% from high = 0
        pct_from_high = float(metrics.get("pct_from_high_20d", -10.0))
        breakout_score = min(100.0, max(0.0, (pct_from_high + 5.0) * 20.0))

        # Quality sub-score (0–100): low ATR% = high score
        atr_pct = float(metrics.get("atr_pct", 5.0))
        quality_score = min(100.0, max(0.0, (5.0 - atr_pct) * 20.0 + 50.0))

        total_weight = w_mom + w_trend + w_vol + w_breakout + w_quality
        composite = (
            mom_score * w_mom
            + trend_score * w_trend
            + vol_score * w_vol
            + breakout_score * w_breakout
            + quality_score * w_quality
        ) / (total_weight if total_weight > 0 else 1.0)

        return round(composite, 2)
