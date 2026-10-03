"""Paper trading: REAL live Binance prices, simulated orders (Adapter for ExchangePort).

Why not just the testnet? The testnet has very few real traders, so its prices
sometimes spike wildly (e.g. a 12% fake "crash" for a few seconds) and trigger
stop-losses that would never trigger on the real market. Paper trading checks
every stop-loss / take-profit against the REAL 1-minute candles instead, and
charges realistic fees and slippage. No API keys needed, no real money.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tradebot.domain.models import Fill, Position, SymbolRules
from tradebot.domain.ports import MarketDataPort
from tradebot.infrastructure.exchange.simulated import SimulatedExchange

_MINUTE = timedelta(minutes=1)


class PaperExchange(SimulatedExchange):
    def __init__(
        self,
        market_data: MarketDataPort,
        rules: dict[str, SymbolRules],
        starting_quote: float,
        quote_asset: str = "USDT",
        fee_pct: float = 0.1,
        slippage_pct: float = 0.05,
    ) -> None:
        super().__init__(rules, starting_quote, _MINUTE, quote_asset, fee_pct, slippage_pct)
        self.market_data = market_data
        self._seen: dict[str, datetime] = {}  # last 1-minute candle checked, per coin

    async def refresh(self, symbol: str) -> None:
        """Replay the real 1-minute candles since the last check (fires stops/targets),
        then take the live price for new market orders."""
        now = datetime.now(UTC)
        since = self._seen.get(symbol, now - 2 * _MINUTE)
        for candle in await self.market_data.fetch_candles(symbol, "1m", since, now):
            if candle.time >= since:
                self.on_candle(symbol, candle)
                self._seen[symbol] = candle.time + _MINUTE
        self._price[symbol] = await self.market_data.fetch_last_price(symbol)
        self._now = now

    # ---------------------------------------------------------- ExchangePort
    async def market_buy(self, symbol: str, quote_amount: float) -> Fill:
        await self.refresh(symbol)
        return await super().market_buy(symbol, quote_amount)

    async def market_sell(self, symbol: str, amount: float) -> Fill:
        await self.refresh(symbol)
        return await super().market_sell(symbol, amount)

    async def fetch_exit_fills(self) -> list[Fill]:
        for symbol in list(self._exits):
            await self.refresh(symbol)
        return await super().fetch_exit_fills()

    async def restore_exit_orders(self, position: Position) -> None:
        """After a restart the simulated wallet is rebuilt from the database."""
        self.balances[self._base(position.symbol)] = (
            self.balances.get(self._base(position.symbol), 0.0) + position.amount
        )
        await super().restore_exit_orders(position)
        # replay real prices since the buy: a stop hit while the bot was off still counts
        self._seen[position.symbol] = position.entry_time
