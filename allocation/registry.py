"""Capital allocation strategy registry."""

from allocation.atr_based import ATRBasedAllocator
from allocation.base import BaseAllocator
from allocation.concentrated import ConcentratedAllocator
from allocation.equal_weight import EqualWeightAllocator
from allocation.kelly import KellyAllocator
from allocation.min_volatility import MinVolatilityAllocator
from allocation.momentum_weighted import MomentumWeightedAllocator
from allocation.rank_decay import RankDecayAllocator
from allocation.risk_parity import RiskParityAllocator
from allocation.score_tiered import ScoreTieredAllocator
from allocation.volatility_targeting import VolatilityTargetingAllocator

ALLOCATORS: dict[str, type[BaseAllocator]] = {
    # Original allocators
    "equal_weight": EqualWeightAllocator,
    "momentum_weighted": MomentumWeightedAllocator,
    "atr_based": ATRBasedAllocator,
    "kelly": KellyAllocator,
    # New allocators
    "risk_parity": RiskParityAllocator,
    "score_tiered": ScoreTieredAllocator,
    "rank_decay": RankDecayAllocator,
    "volatility_targeting": VolatilityTargetingAllocator,
    "concentrated": ConcentratedAllocator,
    "min_volatility": MinVolatilityAllocator,
}


def get_allocator(cfg: dict) -> BaseAllocator:
    name = str(cfg.get("allocation", {}).get("strategy", "equal_weight")).lower()
    cls = ALLOCATORS.get(name)
    if cls is None:
        raise ValueError(f"Unknown allocator: {name!r}. Available: {sorted(ALLOCATORS)}")
    return cls(cfg)
