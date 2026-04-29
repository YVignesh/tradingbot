"""Strategy package."""

from strategies.bollinger_breakout import BollingerBreakoutStrategy
from strategies.ema_crossover import EmaCrossoverStrategy
from strategies.macd_rsi_trend import MacdRsiTrendStrategy
from strategies.registry import STRATEGIES
from strategies.vwap_pullback import VwapPullbackStrategy

__all__ = [
    "STRATEGIES",
    "BollingerBreakoutStrategy",
    "EmaCrossoverStrategy",
    "MacdRsiTrendStrategy",
    "VwapPullbackStrategy",
]
