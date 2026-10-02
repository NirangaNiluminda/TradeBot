"""Trading strategies. Importing this package registers all built-in strategies."""

from tradebot.domain.strategies import breakout, composite, ema_cross, rsi_reversion  # noqa: F401
from tradebot.domain.strategies.base import (
    Strategy,
    StrategyContext,
    StrategyFactory,
    strategy_factory,
)

__all__ = ["Strategy", "StrategyContext", "StrategyFactory", "strategy_factory"]
