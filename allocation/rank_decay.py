"""
allocation/rank_decay.py — Rank-decay allocator
================================================
Allocates capital with exponential decay by screener rank: rank 1 gets the
most, rank N gets the least. The rate of decay is configurable.

Weight(i) = decay_factor ^ (i - 1)   where i is 1-indexed rank

This is useful when you trust the screener's ranking strongly and want
to concentrate capital in the top few picks while still diversifying.

config.json allocation options:
  rank_decay_factor : per-rank multiplier (0.5–0.95 recommended, default 0.75)
                      0.75 → rank2 gets 75% of rank1 capital
                      0.5  → aggressive concentration in rank1
                      0.9  → gentle taper, near-equal weight
"""

from __future__ import annotations

from allocation.base import BaseAllocator


class RankDecayAllocator(BaseAllocator):
    def allocate(self, pool: float, picks: list[dict]) -> dict[str, float]:
        if not picks:
            return {}

        decay = float(self.alloc_cfg.get("rank_decay_factor", 0.75))
        decay = max(0.01, min(0.99, decay))  # clamp to sensible range

        # Sort by rank (ascending) — rank 1 = best
        sorted_picks = sorted(
            picks, key=lambda p: int(p.get("rank", 999))
        )

        weights = [decay ** i for i in range(len(sorted_picks))]
        total = sum(weights)
        return {
            p["symbol"]: pool * (w / total)
            for p, w in zip(sorted_picks, weights)
        }
