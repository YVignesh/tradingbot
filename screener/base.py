"""
screener/base.py — Pluggable screener strategy base class
=========================================================
Each screener defines how to score and rank symbols.
Base metrics (close, atr, avg_volume, momentum_5d, volume_spike, gap_pct)
are computed by filters.py and passed in. Screeners may add extra metrics
and apply their own filters on top of the hard numeric limits.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class BaseScreener(ABC):
    """
    Abstract screener strategy.

    Subclasses implement:
      extra_metrics(hist)   — extra columns computed from raw daily OHLCV history
      passes_filter(m)      — screener-specific accept/reject on top of hard limits
      score(m)              — float ranking score; higher = better
    """

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.scr_cfg: dict = cfg.get("screener", {})

    @abstractmethod
    def extra_metrics(self, hist: pd.DataFrame) -> dict:
        """
        Given the historical daily OHLCV DataFrame (pre-filtered, tail of lookback),
        return a dict of additional metric values for the last bar.
        Return {} if no extra metrics needed.
        """

    @abstractmethod
    def passes_filter(self, metrics: dict) -> bool:
        """
        Return True if the candidate passes screener-specific filters.
        Hard numeric limits (min_price, max_atr etc.) are already applied
        before this is called.
        """

    @abstractmethod
    def score(self, metrics: dict) -> float:
        """Ranking score — higher means the symbol is preferred."""

    def rank(self, candidates: list[dict], top_n: int) -> list[dict]:
        """
        Score, sort, and return top-N candidates with rank assigned.
        Default implementation; subclasses can override for custom tie-breaking.
        """
        for c in candidates:
            c["score"] = round(self.score(c), 4)
        candidates.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        top = candidates[:top_n] if top_n > 0 else candidates
        for i, c in enumerate(top, 1):
            c["rank"] = i
        return top
