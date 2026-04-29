"""
allocation/kelly.py — Kelly criterion allocator
================================================
Computes the Kelly fraction from historical win rate and payoff ratio,
then deploys that fraction of the pool split equally across selected symbols.

Kelly formula: f* = (b·p − q) / b
  b = avg_win / avg_loss  (payoff ratio)
  p = win_rate
  q = 1 − p

config.json allocation options:
  kelly_win_rate  : historical win rate 0–1 (default 0.5)
  kelly_avg_win   : average winning trade in rupees (default 100)
  kelly_avg_loss  : average losing trade in rupees, positive value (default 80)
  kelly_max_frac  : cap on Kelly fraction (default 0.5 = 50% of pool max)
  kelly_fraction  : use fractional Kelly e.g. 0.5 for half-Kelly (default 1.0)

If Kelly fraction is <= 0 (negative edge), falls back to 10% of pool per symbol.
"""

from __future__ import annotations

from allocation.base import BaseAllocator


class KellyAllocator(BaseAllocator):
    def allocate(self, pool: float, picks: list[dict]) -> dict[str, float]:
        if not picks:
            return {}

        win_rate = float(self.alloc_cfg.get("kelly_win_rate", 0.5))
        avg_win = float(self.alloc_cfg.get("kelly_avg_win", 100.0))
        avg_loss = float(self.alloc_cfg.get("kelly_avg_loss", 80.0))
        max_frac = float(self.alloc_cfg.get("kelly_max_frac", 0.5))
        frac_kelly = float(self.alloc_cfg.get("kelly_fraction", 1.0))

        b = avg_win / max(avg_loss, 0.01)
        q = 1.0 - win_rate
        full_kelly = (b * win_rate - q) / b if b > 0 else 0.0
        kelly_f = max(0.0, min(full_kelly * frac_kelly, max_frac))

        if kelly_f <= 0:
            # Negative edge: conservative fallback
            kelly_f = 0.1

        total_deploy = pool * kelly_f
        per = total_deploy / len(picks)
        return {p["symbol"]: per for p in picks}
