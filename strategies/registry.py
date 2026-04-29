"""Shared strategy registry for live trading and backtesting."""

from strategies.base import BaseStrategy
from strategies.bollinger_breakout import BollingerBreakoutStrategy
from strategies.ema_crossover import EmaCrossoverStrategy
from strategies.macd_rsi_trend import MacdRsiTrendStrategy
from strategies.orb import OrbStrategy
from strategies.pivot_bounce import PivotBounceStrategy
from strategies.rsi_reversal import RsiReversalStrategy
from strategies.stochastic_crossover import StochasticCrossoverStrategy
from strategies.supertrend import SupertrendStrategy
from strategies.three_ema_trend import ThreeEmaTrendStrategy
from strategies.vwap_pullback import VwapPullbackStrategy

STRATEGIES: dict[str, type[BaseStrategy]] = {
    # Original strategies
    "ema_crossover": EmaCrossoverStrategy,
    "macd_rsi_trend": MacdRsiTrendStrategy,
    "vwap_pullback": VwapPullbackStrategy,
    "bollinger_breakout": BollingerBreakoutStrategy,
    # New strategies
    "supertrend": SupertrendStrategy,
    "rsi_reversal": RsiReversalStrategy,
    "orb": OrbStrategy,
    "stochastic_crossover": StochasticCrossoverStrategy,
    "three_ema_trend": ThreeEmaTrendStrategy,
    "pivot_bounce": PivotBounceStrategy,
}
