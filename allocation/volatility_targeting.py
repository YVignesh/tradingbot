"""
allocation/volatility_targeting.py — Volatility Targeting allocator
====================================================================
Targets a specific portfolio daily ATR% (e.g. 1% of capital at risk per day)
by scaling position sizes based on each symbol's volatility.

For each symbol:
  target_risk_₹ = pool × target_vol_pct / n_symbols
  position_size = target_risk_₹ / atr_per_share

If volatility is too high, the resulting position is small (but never below
a floor of min_frac × equal_weight). The total deployment may be less than
pool when symbols are particularly volatile.

config.json allocation options:
  vol_target_pct   : target daily portfolio vol as % of pool (default 1.0 = 1%)
  vol_min_frac     : minimum allocation as fraction of equal weight (default 0.2)
"""

from __future__ import annotations

from allocation.base import BaseAllocator


class VolatilityTargetingAllocator(BaseAllocator):
    def allocate(self, pool: float, picks: list[dict]) -> dict[str, float]:
        if not picks:
            return {}

        target_vol_pct = float(self.alloc_cfg.get("vol_target_pct", 1.0)) / 100.0
        min_frac = float(self.alloc_cfg.get("vol_min_frac", 0.2))

        n = len(picks)
        equal_weight = pool / n

        result = {}
        for p in picks:
            atr = max(float(p.get("atr", 1.0)), 0.01)
            close = max(float(p.get("close", 1.0)), 0.01)

            # How many shares we can buy with a given budget
            # Risk per share in ₹ = ATR
            # Target risk per symbol = pool × target_vol_pct / n
            target_risk_per_symbol = pool * target_vol_pct / n

            # Shares = risk / atr_per_share
            shares = target_risk_per_symbol / atr
            # Capital = shares × close
            capital = shares * close

            # Apply floor
            min_capital = equal_weight * min_frac
            result[p["symbol"]] = max(min_capital, min(capital, pool))

        # Scale down if total exceeds pool
        total = sum(result.values())
        if total > pool:
            scale = pool / total
            result = {sym: cap * scale for sym, cap in result.items()}

        return result
