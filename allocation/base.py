"""
allocation/base.py — Pluggable capital allocation base class
============================================================
Each allocator decides how much capital to assign to each selected symbol.
Input: shared pool (float) and ranked picks (list of metric dicts).
Output: {symbol: allocated_capital}. Values may sum to less than pool
(e.g. Kelly under-deploys intentionally); the remainder stays in the pool.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseAllocator(ABC):
    """
    Abstract capital allocator.

    Subclasses implement:
      allocate(pool, picks) → {symbol: capital}

    picks is a list of dicts, each containing at minimum:
      symbol, score, close, atr, momentum_5d, volume_spike, gap_pct
    """

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.alloc_cfg: dict = cfg.get("allocation", {})

    @abstractmethod
    def allocate(self, pool: float, picks: list[dict]) -> dict[str, float]:
        """
        Return {symbol: capital_to_deploy}.
        sum(values) should be <= pool.
        """
