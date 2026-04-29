"""
allocation/equal_weight.py — Equal-weight allocator
====================================================
Splits pool evenly across all selected symbols.
Simple, unbiased, default behaviour.
"""

from __future__ import annotations

from allocation.base import BaseAllocator


class EqualWeightAllocator(BaseAllocator):
    def allocate(self, pool: float, picks: list[dict]) -> dict[str, float]:
        n = len(picks)
        if n == 0:
            return {}
        per = pool / n
        return {p["symbol"]: per for p in picks}
