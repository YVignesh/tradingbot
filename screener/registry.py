"""Screener strategy registry."""

from screener.base import BaseScreener
from screener.breakout import BreakoutScreener
from screener.gap_momentum import GapMomentumScreener
from screener.high_rvol import HighRvolScreener
from screener.mean_reversion import MeanReversionScreener
from screener.momentum import MomentumScreener
from screener.multi_factor import MultiFactorScreener
from screener.price_acceleration import PriceAccelerationScreener
from screener.quality_trend import QualityTrendScreener
from screener.range_position import RangePositionScreener
from screener.vcp import VCPScreener

SCREENERS: dict[str, type[BaseScreener]] = {
    # Original screeners
    "momentum": MomentumScreener,
    "mean_reversion": MeanReversionScreener,
    "breakout": BreakoutScreener,
    # New screeners
    "vcp": VCPScreener,
    "high_rvol": HighRvolScreener,
    "range_position": RangePositionScreener,
    "price_acceleration": PriceAccelerationScreener,
    "multi_factor": MultiFactorScreener,
    "gap_momentum": GapMomentumScreener,
    "quality_trend": QualityTrendScreener,
}


def get_screener(cfg: dict) -> BaseScreener:
    name = str(cfg.get("screener", {}).get("strategy", "momentum")).lower()
    cls = SCREENERS.get(name)
    if cls is None:
        raise ValueError(f"Unknown screener strategy: {name!r}. Available: {sorted(SCREENERS)}")
    return cls(cfg)
