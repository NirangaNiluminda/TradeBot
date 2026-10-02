"""Manual approval of trades (Command pattern).

In MANUAL mode the engine doesn't buy straight away. It creates a
`BuyRequest` command and puts it in this queue. You approve or reject it from
Telegram or the dashboard. Requests expire, because prices move.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import datetime, timedelta

from tradebot.application.event_bus import EventBus
from tradebot.domain.events import Event
from tradebot.domain.models import TradeProposal


@dataclass(frozen=True, slots=True)
class BuyRequest:
    """A buy waiting for your decision."""

    id: str
    proposal: TradeProposal
    reason: str  # why the strategy wants to buy
    created: datetime
    expires: datetime


@dataclass(frozen=True, slots=True)
class ApprovalRequested(Event):
    request: BuyRequest


@dataclass(frozen=True, slots=True)
class ApprovalResolved(Event):
    request: BuyRequest
    outcome: str  # approved / rejected / expired / failed


class ApprovalQueue:
    def __init__(self, bus: EventBus, timeout_minutes: float = 15) -> None:
        self.bus = bus
        self.timeout = timedelta(minutes=timeout_minutes)
        self._pending: dict[str, BuyRequest] = {}
        self._ids = itertools.count(1)

    def request(self, proposal: TradeProposal, reason: str, now: datetime) -> BuyRequest:
        # One pending request per coin: a newer signal replaces the older one.
        for old in [r for r in self._pending.values() if r.proposal.symbol == proposal.symbol]:
            self._resolve(old.id, "replaced", now)
        req = BuyRequest(str(next(self._ids)), proposal, reason, now, now + self.timeout)
        self._pending[req.id] = req
        self.bus.publish(ApprovalRequested(now, req))
        return req

    def take(self, request_id: str, now: datetime) -> BuyRequest | None:
        """Remove and return a request if it is still valid (None if unknown/expired)."""
        self.expire(now)
        return self._pending.pop(request_id, None)

    def reject(self, request_id: str, now: datetime) -> bool:
        return self._resolve(request_id, "rejected", now)

    def resolved(self, req: BuyRequest, outcome: str, now: datetime) -> None:
        self.bus.publish(ApprovalResolved(now, req, outcome))

    def expire(self, now: datetime) -> None:
        for req in [r for r in self._pending.values() if r.expires <= now]:
            self._resolve(req.id, "expired", now)

    def pending(self) -> list[BuyRequest]:
        return sorted(self._pending.values(), key=lambda r: r.created)

    def _resolve(self, request_id: str, outcome: str, now: datetime) -> bool:
        req = self._pending.pop(request_id, None)
        if req is None:
            return False
        self.bus.publish(ApprovalResolved(now, req, outcome))
        return True
