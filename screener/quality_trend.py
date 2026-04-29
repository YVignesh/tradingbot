"""
screener/quality_trend.py — Quality Trend screener
===================================================
Selects stocks in a clean, consistent uptrend: price above both SMA20 and SMA50,
low day-to-day noise (tight ATR% relative to close), and consistent upward direction.
These are the smoothest trending stocks — best for trend-following strategies.

config.json screener options:
  qt_sma_fast        : fast SMA for trend stack (default 20)
  qt_sma_slow        : slow SMA for trend stack (default 50)
  qt_max_atr_pct     : maximum ATR as % of close for "smooth" trend (default 3.0)
  qt_min_up_days     : minimum number of up-close days in last 10 (default 6)
  qt_min_momentum    : minimum 5-day momentum % (default 1.0)
"""

from __future__ import annotations

import pandas as pd

from screener.base import BaseScreener


class QualityTrendScreener(BaseScreener):
    """Selects stocks in clean, consistent, low-noise uptrends."""

    def extra_metrics(self, hist: pd.DataFrame) -> dict:
        sma_fast = int(self.scr_cfg.get("qt_sma_fast", 20))
        sma_slow = int(self.scr_cfg.get("qt_sma_slow", 50))

        if len(hist) < max(sma_slow, 14):
            return {}

        close = hist["close"]
        high = hist["high"]
        low = hist["low"]

        sma20 = float(close.rolling(min(sma_fast, len(hist)), min_periods=10).mean().iloc[-1])
        sma50 = float(close.rolling(min(sma_slow, len(hist)), min_periods=20).mean().iloc[-1])
        price = float(close.iloc[-1])

        # Trend stack check
        trend_stack = price > sma20 > sma50

        # ATR%: daily ATR as % of close — lower is smoother
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr_14 = float(tr.tail(14).mean())
        atr_pct = atr_14 / price * 100 if price > 0 else 5.0

        # Consistency: count up-close days in last 10
        up_days = int((close.diff().tail(10) > 0).sum())

        # Trend strength: price % above SMA50
        pct_above_slow = (price / sma50 - 1.0) * 100 if sma50 > 0 else 0.0

        return {
            "trend_stack": trend_stack,
            "atr_pct": round(atr_pct, 3),
            "up_days_10": up_days,
            "pct_above_sma50": round(pct_above_slow, 2),
        }

    def passes_filter(self, metrics: dict) -> bool:
        max_atr_pct = float(self.scr_cfg.get("qt_max_atr_pct", 3.0))
        min_up_days = int(self.scr_cfg.get("qt_min_up_days", 6))
        min_mom = float(self.scr_cfg.get("qt_min_momentum", 1.0))

        if not metrics.get("trend_stack", False):
            return False
        if float(metrics.get("atr_pct", 99.0)) > max_atr_pct:
            return False
        if int(metrics.get("up_days_10", 0)) < min_up_days:
            return False
        if float(metrics.get("momentum_5d", 0.0)) < min_mom:
            return False
        return True

    def score(self, metrics: dict) -> float:
        mom = float(metrics.get("momentum_5d", 0.0))
        pct_above = float(metrics.get("pct_above_sma50", 0.0))
        up_days = float(metrics.get("up_days_10", 0))
        atr_pct = float(metrics.get("atr_pct", 5.0))
        vol_spike = float(metrics.get("volume_spike", 1.0))
        # More momentum, more up days, lower noise = higher score
        return (
            mom * 0.5
            + pct_above * 2.0
            + up_days * 3.0
            - atr_pct * 5.0
            + vol_spike * 5.0
        )
