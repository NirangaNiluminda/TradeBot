"""Saves what the engine does into the repository (an event-bus listener).

The engine never talks to the database directly — it just publishes events.
"""

from __future__ import annotations

from tradebot.application.event_bus import EventBus
from tradebot.domain.events import PositionClosed, PositionOpened
from tradebot.domain.models import Position, Trade
from tradebot.domain.ports import TradeRepository


class TradeRecorder:
    def __init__(self, repo: TradeRepository, bus: EventBus) -> None:
        self.repo = repo
        bus.subscribe(PositionOpened, lambda e: repo.save_position(e.position))
        bus.subscribe(PositionClosed, self._closed)

    def _closed(self, event: PositionClosed) -> None:
        self.repo.delete_position(event.trade.symbol)
        self.repo.save_trade(event.trade)


def ledger_cash(budget: float, trades: list[Trade], open_positions: list[Position]) -> float:
    """Rebuild the bot's cash after a restart: budget + realised profit - open positions."""
    return (
        budget
        + sum(t.pnl for t in trades)
        - sum(p.amount * p.entry_price + p.entry_fee for p in open_positions)
    )
