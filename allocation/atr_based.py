"""
allocation/atr_based.py — Inverse-ATR (volatility-adjusted) allocator
======================================================================
Lower-volatility symbols receive more capital; higher-volatility symbols
receive less. Inverse of ATR is used as the weight.
This is a simple form of risk-parity: each symbol contributes roughly
equal point-risk to the portfolio.
"""

from __future__ import annotations

from allocation.base import BaseAllocator


class ATRBasedAllocator(BaseAllocator):
    def allocate(self, pool: float, picks: list[dict]) -> dict[str, float]:
        if not picks:
            return {}
        inv_atrs = [1.0 / max(float(p.get("atr", 1.0)), 0.01) for p in picks]
        total = sum(inv_atrs)
        return {p["symbol"]: pool * (ia / total) for p, ia in zip(picks, inv_atrs)}
