from __future__ import annotations

from typing import Protocol

from tradebot.domain.models import Position, Trade


class TradeRepository(Protocol):
    """Remembers open positions and finished trades, so a restart loses nothing."""

    def save_position(self, position: Position) -> None: ...

    def delete_position(self, symbol: str) -> None: ...

    def load_positions(self) -> list[Position]: ...

    def save_trade(self, trade: Trade) -> None: ...

    def load_trades(self) -> list[Trade]: ...
