"""
screener/mean_reversion.py — Mean-reversion screener strategy
=============================================================
Selects oversold stocks that have pulled back to support levels.
Extra metrics: RSI(14), pct distance from 20-day SMA, Bollinger %B.

config.json screener options:
  rsi_threshold   : max RSI to pass filter (default 40)
  pct_below_sma   : must be at least this % below SMA20 (default 0 = any below)
"""

from __future__ import annotations

import pandas as pd

from indicators.momentum import rsi as compute_rsi
from indicators.trend import sma
from indicators.volatility import bollinger_bands
from screener.base import BaseScreener


class MeanReversionScreener(BaseScreener):
    """Picks oversold pullbacks: low RSI, below 20d SMA, near lower Bollinger Band."""

    def extra_metrics(self, hist: pd.DataFrame) -> dict:
        if len(hist) < 20:
            return {}

        rsi_s = compute_rsi(hist["close"], 14)
        sma20 = sma(hist["close"], 20)
        bb = bollinger_bands(hist["close"], 20, 2.0)

        rsi_val = float(rsi_s.iloc[-1]) if not pd.isna(rsi_s.iloc[-1]) else 50.0
        sma20_val = float(sma20.iloc[-1]) if not pd.isna(sma20.iloc[-1]) else float(hist["close"].iloc[-1])
        close = float(hist["close"].iloc[-1])

        pct_from_sma20 = ((close - sma20_val) / sma20_val) * 100 if sma20_val > 0 else 0.0

        upper = float(bb.upper.iloc[-1]) if not pd.isna(bb.upper.iloc[-1]) else close + 1
        lower = float(bb.lower.iloc[-1]) if not pd.isna(bb.lower.iloc[-1]) else close - 1
        band_width = upper - lower
        bb_pct_b = ((close - lower) / band_width) if band_width > 0 else 0.5

        return {
            "rsi_14": round(rsi_val, 2),
            "pct_from_sma20": round(pct_from_sma20, 2),
            "bb_pct_b": round(bb_pct_b, 4),
        }

    def passes_filter(self, metrics: dict) -> bool:
        rsi_threshold = float(self.scr_cfg.get("rsi_threshold", 40.0))
        pct_below_required = float(self.scr_cfg.get("pct_below_sma", 0.0))

        rsi_val = float(metrics.get("rsi_14", 50.0))
        pct_from_sma = float(metrics.get("pct_from_sma20", 0.0))

        if rsi_val > rsi_threshold:
            return False
        if pct_below_required > 0 and pct_from_sma > -pct_below_required:
            return False
        return True

    def score(self, metrics: dict) -> float:
        rsi_threshold = float(self.scr_cfg.get("rsi_threshold", 40.0))
        rsi_val = float(metrics.get("rsi_14", 50.0))
        pct_from_sma = float(metrics.get("pct_from_sma20", 0.0))
        bb_pct_b = float(metrics.get("bb_pct_b", 0.5))
        # Higher score = more oversold (low RSI, far below SMA, near lower band)
        return (
            (rsi_threshold - rsi_val) * 1.5
            + (-pct_from_sma) * 2.0
            + (0.5 - bb_pct_b) * 20.0
        )
