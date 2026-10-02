from __future__ import annotations

from datetime import datetime
from typing import Protocol

from tradebot.domain.models import Candle, SymbolRules


class MarketDataPort(Protocol):
    """Read-only public market information (no API keys needed)."""

    async def fetch_candles(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> list[Candle]:
        """Return CLOSED candles whose open time is in [start, end), oldest first."""
        ...

    async def fetch_symbol_rules(self, symbol: str) -> SymbolRules:
        """Return min order size / quantity step for a trading pair."""
        ...
