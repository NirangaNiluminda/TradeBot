"""A tiny publish/subscribe event bus (Observer pattern)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any, TypeVar

from tradebot.domain.events import Event

E = TypeVar("E", bound=Event)


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[type, list[Callable[[Any], None]]] = defaultdict(list)

    def subscribe(self, event_type: type[E], handler: Callable[[E], None]) -> None:
        """Call `handler` for every published event of `event_type` (or a subclass)."""
        self._handlers[event_type].append(handler)

    def publish(self, event: Event) -> None:
        for cls in type(event).__mro__:
            for handler in self._handlers.get(cls, ()):
                handler(event)
