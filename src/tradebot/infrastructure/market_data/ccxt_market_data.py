"""Binance (or any ccxt exchange) public market data — Adapter for MarketDataPort.

No API keys needed: candles and trading rules are public information.
"""

from __future__ import annotations

from datetime import UTC, datetime

import ccxt.async_support as ccxt

from tradebot.domain.models import Candle, SymbolRules
from tradebot.domain.timeframes import timeframe_seconds

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
            rows = await self._exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=_PAGE)
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

    async def fetch_symbol_rules(self, symbol: str) -> SymbolRules:
        if not self._markets_loaded:
            await self._exchange.load_markets()
            self._markets_loaded = True
        market = self._exchange.market(symbol)
        step = market["precision"]["amount"]
        if self._exchange.precisionMode == ccxt.DECIMAL_PLACES:
            step = 10 ** -int(step)
        limits = market["limits"]
        return SymbolRules(
            symbol=symbol,
            min_notional=float(limits["cost"]["min"] or 0.0),
            amount_step=float(step),
            min_amount=float(limits["amount"]["min"] or 0.0),
        )

    async def close(self) -> None:
        await self._exchange.close()


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)
