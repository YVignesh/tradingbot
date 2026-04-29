"""
allocation/concentrated.py — Concentrated top-pick allocator
=============================================================
Puts a fixed percentage of the pool into the #1 ranked pick, a smaller
percentage into #2, and splits the rest equally across remaining symbols.

This is a conviction-weighted approach — you trust the screener's top
pick most and want meaningful size there while keeping exposure to others.

config.json allocation options:
  conc_top1_pct    : fraction of pool for rank-1 (default 0.4 = 40%)
  conc_top2_pct    : fraction of pool for rank-2 (default 0.25 = 25%)
                     Set to 0 to only concentrate in rank-1.
  conc_min_symbols : minimum picks needed to apply concentration (default 3)
                     Below this, falls back to equal weight.
"""

from __future__ import annotations

from allocation.base import BaseAllocator


class ConcentratedAllocator(BaseAllocator):
    def allocate(self, pool: float, picks: list[dict]) -> dict[str, float]:
        if not picks:
            return {}

        top1_pct = float(self.alloc_cfg.get("conc_top1_pct", 0.40))
        top2_pct = float(self.alloc_cfg.get("conc_top2_pct", 0.25))
        min_symbols = int(self.alloc_cfg.get("conc_min_symbols", 3))

        n = len(picks)

        # Fallback to equal weight if too few symbols
        if n < min_symbols:
            eq = pool / n
            return {p["symbol"]: eq for p in picks}

        sorted_picks = sorted(picks, key=lambda p: int(p.get("rank", 999)))

        result = {}
        allocated = 0.0

        if n >= 1:
            result[sorted_picks[0]["symbol"]] = pool * top1_pct
            allocated += pool * top1_pct

        if n >= 2 and top2_pct > 0:
            result[sorted_picks[1]["symbol"]] = pool * top2_pct
            allocated += pool * top2_pct

        remaining = pool - allocated
        remaining_picks = sorted_picks[2:] if top2_pct > 0 and n >= 2 else sorted_picks[1:]
        if remaining_picks and remaining > 0:
            per_symbol = remaining / len(remaining_picks)
            for p in remaining_picks:
                result[p["symbol"]] = per_symbol

        return result
