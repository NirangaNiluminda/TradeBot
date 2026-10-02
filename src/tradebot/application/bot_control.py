"""Bot on/off switch (State pattern).

    RUNNING  -> trades normally
    PAUSED   -> no NEW buys; open positions keep their stop-loss/take-profit
    STOPPED  -> kill switch: positions closed, trading loop ends (final)

Each state object answers "what is allowed?", so the rest of the code never
needs if/else chains over state names.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from tradebot.application.event_bus import EventBus
from tradebot.domain.events import Event


class BotState:
    name: str = ""
    allows_new_trades: bool = False
    keeps_running: bool = True

    def __repr__(self) -> str:
        return self.name


class Running(BotState):
    name, allows_new_trades = "RUNNING", True


class Paused(BotState):
    name = "PAUSED"


class Stopped(BotState):
    name, keeps_running = "STOPPED", False


@dataclass(frozen=True, slots=True)
class BotStateChanged(Event):
    previous: str
    current: str
    reason: str


class InvalidTransition(Exception):
    pass


class BotControl:
    def __init__(
        self, bus: EventBus | None = None, initial: BotState | None = None, manual: bool = False
    ) -> None:
        self.bus = bus
        self.state: BotState = initial or Running()
        # manual = every buy waits for your approval (Telegram / dashboard)
        self.manual_approval = manual

    def set_manual_approval(self, manual: bool) -> None:
        self.manual_approval = manual
        if self.bus:
            mode = "MANUAL approval" if manual else "AUTO"
            name = self.state.name
            self.bus.publish(BotStateChanged(datetime.now(UTC), name, name, f"trade mode: {mode}"))

    def pause(self, reason: str = "paused by user") -> None:
        self._move(Paused(), reason, allowed_from=(Running,))

    def resume(self, reason: str = "resumed by user") -> None:
        self._move(Running(), reason, allowed_from=(Paused,))

    def stop(self, reason: str = "kill switch") -> None:
        self._move(Stopped(), reason, allowed_from=(Running, Paused))

    def _move(self, new: BotState, reason: str, allowed_from: tuple[type[BotState], ...]) -> None:
        if not isinstance(self.state, allowed_from):
            raise InvalidTransition(f"cannot go from {self.state.name} to {new.name}")
        old, self.state = self.state, new
        if self.bus:
            self.bus.publish(BotStateChanged(datetime.now(UTC), old.name, new.name, reason))
