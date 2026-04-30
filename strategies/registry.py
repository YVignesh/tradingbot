"""Shared strategy registry for live trading and backtesting."""

from strategies.base import BaseStrategy
from strategies.bollinger_breakout import BollingerBreakoutStrategy
from strategies.ema_crossover import EmaCrossoverStrategy
from strategies.gap_and_go import GapAndGoStrategy
from strategies.inside_bar import InsideBarStrategy
from strategies.macd_divergence import MacdDivergenceStrategy
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
    # Trend-following
    "supertrend": SupertrendStrategy,
    "three_ema_trend": ThreeEmaTrendStrategy,
    # Mean-reversion
    "rsi_reversal": RsiReversalStrategy,
    "stochastic_crossover": StochasticCrossoverStrategy,
    # Intraday patterns
    "orb": OrbStrategy,
    "pivot_bounce": PivotBounceStrategy,
    "inside_bar": InsideBarStrategy,
    "gap_and_go": GapAndGoStrategy,
    # Advanced
    "macd_divergence": MacdDivergenceStrategy,
}
