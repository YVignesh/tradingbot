"""
screener/institutional.py — Institutional Activity Screener
=============================================================
Screens for stocks with high delivery percentage (proxy for
institutional buying) combined with price and volume momentum.

On NSE, delivery percentage > 50% on a breakout day often indicates
institutional accumulation. This screener uses delivery % as a proxy.

Note: Delivery % data requires yfinance or NSE API. Falls back to
volume-based estimation if unavailable.

Scoring:
  delivery_pct_bonus × 2 + volume_spike × 15 + momentum_5d × 0.5

Config keys:
  min_delivery_pct : minimum delivery % to pass (default 40)
"""

from __future__ import annotations

import pandas as pd
import numpy as np

from screener.base import BaseScreener


class InstitutionalScreener(BaseScreener):
    """Screens for institutional activity using delivery % and volume."""

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.min_delivery_pct = float(self.scr_cfg.get("min_delivery_pct", 40))

    def extra_metrics(self, hist: pd.DataFrame) -> dict:
        if len(hist) < 5:
            return {}

        # Estimate delivery % from volume patterns
        # True delivery data needs NSE Bhavcopy or yfinance.
        # Proxy: low intraday range relative to volume = institutional accumulation.
        close = hist["close"].iloc[-1]
        open_ = hist["open"].iloc[-1]
        high = hist["high"].iloc[-1]
        low = hist["low"].iloc[-1]
        volume = hist["volume"].iloc[-1]

        if high == low or volume == 0:
            return {"est_delivery_pct": 0.0}

        # Body/range ratio: larger body with smaller wicks = directional conviction
        body = abs(close - open_)
        total_range = high - low
        body_ratio = body / total_range if total_range > 0 else 0

        # Volume trend: rising volume on up-days = accumulation
        avg_vol_5 = float(hist["volume"].tail(5).mean())
        avg_vol_20 = float(hist["volume"].tail(20).mean()) if len(hist) >= 20 else avg_vol_5
        vol_trend = avg_vol_5 / avg_vol_20 if avg_vol_20 > 0 else 1.0

        # Consecutive up-days
        close_series = hist["close"].tail(5)
        up_days = sum(1 for i in range(1, len(close_series)) if close_series.iloc[i] > close_series.iloc[i - 1])

        # Estimated delivery % (proxy: body_ratio × vol_trend × up_day_factor)
        est_delivery = min(90, max(10, body_ratio * 50 + vol_trend * 15 + up_days * 5))

        # Price position relative to 20-day SMA
        sma_20 = float(hist["close"].tail(20).mean()) if len(hist) >= 20 else float(close)
        pct_above_sma20 = (float(close) - sma_20) / sma_20 * 100 if sma_20 > 0 else 0

        return {
            "est_delivery_pct": round(est_delivery, 1),
            "vol_trend_5_20": round(vol_trend, 2),
            "up_days_5": up_days,
            "pct_above_sma20": round(pct_above_sma20, 2),
        }

    def passes_filter(self, metrics: dict) -> bool:
        delivery = metrics.get("est_delivery_pct", 0)
        return delivery >= self.min_delivery_pct

    def score(self, metrics: dict) -> float:
        delivery = metrics.get("est_delivery_pct", 0)
        vol_trend = metrics.get("vol_trend_5_20", 1.0)
        vol_spike = metrics.get("vol_spike", 1.0)
        mom_5d = metrics.get("mom_5d", 0)
        up_days = metrics.get("up_days_5", 0)
        pct_above = metrics.get("pct_above_sma20", 0)

        return (
            delivery * 0.3
            + vol_spike * 15
            + vol_trend * 10
            + mom_5d * 0.5
            + up_days * 3
            + max(0, pct_above) * 0.5
        )
