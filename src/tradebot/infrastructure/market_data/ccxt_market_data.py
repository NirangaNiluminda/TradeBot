"""Binance (or any ccxt exchange) public market data — Adapter for MarketDataPort.

No API keys needed: candles and trading rules are public information.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import ccxt.async_support as ccxt

from tradebot.domain.models import Candle, SymbolRules
from tradebot.domain.timeframes import timeframe_seconds
from tradebot.infrastructure.ccxt_common import symbol_rules

_PAGE = 1000  # Binance returns at most 1000 candles per request


class CcxtMarketData:
    def __init__(self, exchange_id: str = "binance", sandbox: bool = False) -> None:
        self._exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})
        if sandbox:
            self._exchange.set_sandbox_mode(True)
        self._markets_loaded = False

    async def fetch_candles(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> list[Candle]:
        step_ms = timeframe_seconds(timeframe) * 1000
        since = _ms(start)
        end_ms = _ms(end)
        # Only candles that have fully closed
        now_ms = _ms(datetime.now(UTC))
        candles: list[Candle] = []
        while since < end_ms:
            rows = await self._page(symbol, timeframe, since)
            if not rows:
                break
            for t, o, h, low, c, v in rows:
                if t >= end_ms or t + step_ms > now_ms:
                    continue
                candles.append(Candle(_dt(t), float(o), float(h), float(low), float(c), float(v)))
            next_since = rows[-1][0] + step_ms
            if next_since <= since:
                break
            since = next_since
        return candles

    async def _page(self, symbol: str, timeframe: str, since: int, attempts: int = 5) -> list:
        """One page of candles; retries network hiccups (long downloads = many pages)."""
        for attempt in range(1, attempts + 1):
            try:
                return await self._exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=_PAGE)
            except ccxt.NetworkError:
                if attempt == attempts:
                    raise
                await asyncio.sleep(2 * attempt)
        return []

    async def fetch_symbol_rules(self, symbol: str) -> SymbolRules:
        if not self._markets_loaded:
            await self._exchange.load_markets()
            self._markets_loaded = True
        return symbol_rules(self._exchange, symbol)

    async def fetch_last_price(self, symbol: str) -> float:
        ticker = await self._exchange.fetch_ticker(symbol)
        return float(ticker["last"])

    async def close(self) -> None:
        await self._exchange.close()


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)
