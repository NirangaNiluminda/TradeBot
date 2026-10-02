"""Ports = contracts (interfaces) between the core logic and the outside world.

The core only ever depends on these. Concrete implementations ("adapters")
live in `tradebot.infrastructure` and are wired together in `tradebot.bootstrap`.
"""

from tradebot.domain.ports.exchange import ExchangePort, OrderRejected
from tradebot.domain.ports.llm import LLMError, LLMPort, LLMRateLimited
from tradebot.domain.ports.market_data import MarketDataPort
from tradebot.domain.ports.market_risk import MarketRiskPort
from tradebot.domain.ports.news import NewsSourcePort

__all__ = [
    "ExchangePort",
    "LLMError",
    "LLMPort",
    "LLMRateLimited",
    "MarketDataPort",
    "MarketRiskPort",
    "NewsSourcePort",
    "OrderRejected",
]
