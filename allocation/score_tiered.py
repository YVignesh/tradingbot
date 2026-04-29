"""
allocation/score_tiered.py — Score-tiered allocator
====================================================
Divides selected symbols into tiers by screener score and allocates
proportionally within each tier. Top tier receives a multiplier above
equal weight; bottom tier receives a fraction.

Default 3-tier split:
  Tier 1 (top 33%):   2× equal weight
  Tier 2 (middle 33%): 1× equal weight
  Tier 3 (bottom 33%): 0.5× equal weight

config.json allocation options:
  tier_count        : number of tiers (default 3)
  tier_top_mult     : capital multiplier for top tier (default 2.0)
  tier_bottom_mult  : capital multiplier for bottom tier (default 0.5)
"""

from __future__ import annotations

from allocation.base import BaseAllocator


class ScoreTieredAllocator(BaseAllocator):
    def allocate(self, pool: float, picks: list[dict]) -> dict[str, float]:
        if not picks:
            return {}

        n = len(picks)
        tier_count = int(self.alloc_cfg.get("tier_count", 3))
        top_mult = float(self.alloc_cfg.get("tier_top_mult", 2.0))
        bot_mult = float(self.alloc_cfg.get("tier_bottom_mult", 0.5))

        # Sort by score descending (picks already ranked, but re-sort to be safe)
        sorted_picks = sorted(picks, key=lambda p: float(p.get("score", 0.0)), reverse=True)

        # Assign tier multipliers
        tier_size = max(1, n // tier_count)
        multipliers = []
        for i, p in enumerate(sorted_picks):
            tier_idx = min(i // tier_size, tier_count - 1)
            if tier_idx == 0:
                mult = top_mult
            elif tier_idx == tier_count - 1:
                mult = bot_mult
            else:
                # Linear interpolation for middle tiers
                mult = top_mult - (top_mult - bot_mult) * tier_idx / (tier_count - 1)
            multipliers.append(mult)

        # Normalise so total deployment = pool
        base = pool / sum(multipliers) if sum(multipliers) > 0 else pool / n
        return {p["symbol"]: base * m for p, m in zip(sorted_picks, multipliers)}
