"""
screener/price_acceleration.py — Price Acceleration screener
=============================================================
Identifies stocks where short-term momentum is accelerating — the rate of
change is itself increasing. This catches early-stage moves before they
show up in longer-horizon momentum screens.

Acceleration = today's 1-day return minus the trailing average daily return.
Positive acceleration means the stock is outperforming its own recent pace.

config.json screener options:
  accel_period    : lookback for baseline average daily return (default 10)
  accel_min       : minimum acceleration % to qualify (default 0.3)
  accel_vol_min   : minimum volume spike to confirm (default 1.2)
"""

from __future__ import annotations

import pandas as pd

from screener.base import BaseScreener


class PriceAccelerationScreener(BaseScreener):
    """Selects stocks where recent price momentum is picking up pace."""

    def extra_metrics(self, hist: pd.DataFrame) -> dict:
        period = int(self.scr_cfg.get("accel_period", 10))
        if len(hist) < period + 2:
            return {}

        close = hist["close"]
        daily_returns = close.pct_change() * 100  # %

        # Baseline: trailing average daily return
        avg_return = float(daily_returns.tail(period).mean())
        # Today's return
        today_return = float(daily_returns.iloc[-1])

        acceleration = today_return - avg_return

        # 3-day return for additional signal
        ret_3d = float((close.iloc[-1] / close.iloc[-4] - 1) * 100) if len(hist) >= 4 else 0.0

        return {
            "today_return": round(today_return, 3),
            "avg_return": round(avg_return, 3),
            "acceleration": round(acceleration, 3),
            "ret_3d": round(ret_3d, 3),
        }

    def passes_filter(self, metrics: dict) -> bool:
        min_accel = float(self.scr_cfg.get("accel_min", 0.3))
        min_rvol = float(self.scr_cfg.get("accel_vol_min", 1.2))

        if float(metrics.get("acceleration", 0.0)) < min_accel:
            return False
        if float(metrics.get("volume_spike", 0.0)) < min_rvol:
            return False
        return True

    def score(self, metrics: dict) -> float:
        accel = float(metrics.get("acceleration", 0.0))
        vol_spike = float(metrics.get("volume_spike", 1.0))
        ret_3d = float(metrics.get("ret_3d", 0.0))
        return accel * 25.0 + vol_spike * 10.0 + ret_3d * 1.5
