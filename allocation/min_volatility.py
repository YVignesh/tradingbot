"""
allocation/min_volatility.py — Minimum Volatility allocator
============================================================
Selects the N least volatile symbols from picks and allocates equally
among them, ignoring higher-volatility picks entirely.

"Minimum volatility" here means minimum ATR% (ATR/close), which is a
practical proxy for daily price swings. This is useful when capital
preservation is the priority and you want smooth, predictable positions.

config.json allocation options:
  minvol_top_n     : number of lowest-volatility picks to include (default: all picks)
  minvol_equal     : if true, equal weight among selected; if false, inverse-vol weight (default true)
"""

from __future__ import annotations

from allocation.base import BaseAllocator


class MinVolatilityAllocator(BaseAllocator):
    def allocate(self, pool: float, picks: list[dict]) -> dict[str, float]:
        if not picks:
            return {}

        top_n = int(self.alloc_cfg.get("minvol_top_n", len(picks)))
        equal_weight = bool(self.alloc_cfg.get("minvol_equal", True))

        # Sort by ATR% ascending (lowest vol first)
        def atr_pct(p: dict) -> float:
            atr = max(float(p.get("atr", 1.0)), 0.01)
            close = max(float(p.get("close", 1.0)), 0.01)
            return atr / close

        sorted_picks = sorted(picks, key=atr_pct)
        selected = sorted_picks[:max(1, top_n)]

        if equal_weight:
            per = pool / len(selected)
            return {p["symbol"]: per for p in selected}
        else:
            inv_vols = [1.0 / max(atr_pct(p), 1e-6) for p in selected]
            total = sum(inv_vols)
            return {p["symbol"]: pool * (iv / total) for p, iv in zip(selected, inv_vols)}
