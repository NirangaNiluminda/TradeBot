from __future__ import annotations

from typing import Protocol

from tradebot.domain.models import Fill, Position, SymbolRules


class OrderRejected(Exception):
    """The exchange refused an order (too small, not enough balance, ...)."""


class ExchangePort(Protocol):
    """Everything the bot needs to trade. Implemented by a simulator (backtest)
    and by the real Binance adapter (testnet / live)."""

    async def get_free_balance(self, asset: str) -> float:
        """Free (not locked in orders) balance of an asset, e.g. 'USDT'."""
        ...

    async def get_symbol_rules(self, symbol: str) -> SymbolRules: ...

    async def market_buy(self, symbol: str, quote_amount: float) -> Fill:
        """Buy for roughly `quote_amount` USDT at the current market price."""
        ...

    async def market_sell(self, symbol: str, amount: float) -> Fill: ...

    async def place_exit_orders(
        self, symbol: str, amount: float, stop_loss: float, take_profit: float
    ) -> str:
        """Place automatic stop-loss + take-profit exits (an OCO order on Binance:
        'One Cancels the Other' — when one fills, the other is cancelled).
        Returns the exchange's order id."""
        ...

    async def restore_exit_orders(self, position: Position) -> None:
        """After a restart: start watching a position's existing exit orders again."""
        ...

    async def cancel_exit_orders(self, symbol: str) -> None: ...

    async def fetch_exit_fills(self) -> list[Fill]:
        """Exit orders that filled since the last call (stop-loss / take-profit hits)."""
        ...
