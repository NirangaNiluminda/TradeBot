"""Trading strategies. Importing this package registers all built-in strategies."""

from tradebot.domain.strategies import (  # noqa: F401
    breakout,
    composite,
    ema_cross,
    rsi_reversion,
    trend_ensemble,
)
from tradebot.domain.strategies.base import (
    Strategy,
    StrategyContext,
    StrategyFactory,
    strategy_factory,
)

__all__ = ["Strategy", "StrategyContext", "StrategyFactory", "strategy_factory"]
