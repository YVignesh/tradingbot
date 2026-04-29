"""
allocation/momentum_weighted.py — Score-proportional allocator
==============================================================
Symbols with higher screener scores receive more capital.
Scores are shifted so the minimum is 0.01 (handles negative scores).
"""

from __future__ import annotations

from allocation.base import BaseAllocator


class MomentumWeightedAllocator(BaseAllocator):
    def allocate(self, pool: float, picks: list[dict]) -> dict[str, float]:
        if not picks:
            return {}
        raw_scores = [float(p.get("score", 0.0)) for p in picks]
        # Shift so all weights are positive
        min_score = min(raw_scores)
        weights = [s - min_score + 0.01 for s in raw_scores]
        total = sum(weights)
        return {p["symbol"]: pool * (w / total) for p, w in zip(picks, weights)}
