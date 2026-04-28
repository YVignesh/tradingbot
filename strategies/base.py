"""
strategies/base.py — BaseStrategy Abstract Class
==================================================
All strategies inherit from BaseStrategy and implement the required
methods. main.py calls these methods uniformly regardless of strategy.
"""
from abc import ABC, abstractmethod
from typing import Optional


class BaseStrategy(ABC):

    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    def on_tick(self, tick: dict) -> None:
        """Called on every incoming market tick from MarketFeed."""
        ...

    @abstractmethod
    def generate_signal(self, session) -> Optional[str]:
        """Return 'BUY', 'SELL', 'SHORT', 'COVER', or None based on current indicators."""
        ...

    def on_fill(self, order_update: dict) -> None:
        """Called when an order fill arrives from OrderFeed. Override if needed."""

    def on_start(self, session) -> None:
        """Called once after login, before the strategy loop begins."""

    def on_stop(self) -> None:
        """Called on graceful shutdown before logout."""
