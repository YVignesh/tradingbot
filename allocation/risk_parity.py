"""
allocation/risk_parity.py — Risk Parity allocator
==================================================
Allocates capital so that each symbol contributes equal *percentage* risk
to the portfolio, using ATR/close as the per-symbol volatility proxy.

Unlike atr_based (which uses raw ATR points), this uses ATR% — so a ₹500
stock with ATR₹15 (3%) and a ₹50 stock with ATR₹1.5 (3%) get equal capital.
This is the correct form of inverse-vol weighting when symbols differ in price.

config.json allocation options:
  (none — automatic from pick metrics)
"""

from __future__ import annotations

from allocation.base import BaseAllocator


class RiskParityAllocator(BaseAllocator):
    def allocate(self, pool: float, picks: list[dict]) -> dict[str, float]:
        if not picks:
            return {}

        inv_vol = []
        for p in picks:
            atr = max(float(p.get("atr", 1.0)), 0.01)
            close = max(float(p.get("close", 1.0)), 0.01)
            vol_pct = atr / close          # ATR as % of price
            inv_vol.append(1.0 / max(vol_pct, 1e-6))

        total = sum(inv_vol)
        return {p["symbol"]: pool * (iv / total) for p, iv in zip(picks, inv_vol)}
