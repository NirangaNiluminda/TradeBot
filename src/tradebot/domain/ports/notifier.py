from __future__ import annotations

from typing import Protocol


class NotifierPort(Protocol):
    """Somewhere to send human-readable messages (console, Telegram, ...)."""

    async def send(self, text: str) -> None: ...
