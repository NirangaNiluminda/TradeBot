"""Keeps the last N things that happened, as readable lines (for the dashboard)."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime

from tradebot.application.approvals import ApprovalRequested, ApprovalResolved
from tradebot.application.bot_control import BotStateChanged
from tradebot.application.event_bus import EventBus
from tradebot.application.explainer import ExplanationReady
from tradebot.application.notifications import describe_closed, describe_opened, describe_risk
from tradebot.domain.events import (
    PositionClosed,
    PositionOpened,
    RiskChanged,
    SignalGenerated,
    TradeRejected,
)


class EventLog:
    def __init__(self, bus: EventBus, size: int = 200) -> None:
        self.lines: deque[tuple[datetime, str, str]] = deque(maxlen=size)
        add = self._add
        bus.subscribe(PositionOpened, lambda e: add("trade", describe_opened(e)))
        bus.subscribe(PositionClosed, lambda e: add("trade", describe_closed(e)))
        bus.subscribe(RiskChanged, lambda e: add("risk", describe_risk(e)))
        bus.subscribe(BotStateChanged, lambda e: add("bot", f"Bot {e.current}: {e.reason}"))
        bus.subscribe(
            SignalGenerated,
            lambda e: add("signal", f"{e.symbol} {e.signal.action.value}: {e.signal.reason}"),
        )
        bus.subscribe(TradeRejected, lambda e: add("blocked", f"{e.symbol}: {e.reason}"))
        bus.subscribe(
            ApprovalRequested,
            lambda e: add("approval", f"Approval needed: buy {e.request.proposal.symbol}"),
        )
        bus.subscribe(
            ApprovalResolved,
            lambda e: add("approval", f"Request {e.request.id} {e.outcome}"),
        )
        bus.subscribe(ExplanationReady, lambda e: add("explain", f"💬 {e.symbol}: {e.text}"))

    def _add(self, kind: str, text: str) -> None:
        self.lines.append((datetime.now(UTC), kind, text))

    def recent(self, n: int = 50) -> list[dict]:
        return [{"time": t.isoformat(), "kind": k, "text": x} for t, k, x in list(self.lines)[-n:]][
            ::-1
        ]
