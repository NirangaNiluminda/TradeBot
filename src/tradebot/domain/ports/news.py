from __future__ import annotations

from typing import Protocol

from tradebot.domain.models import NewsItem


class NewsSourcePort(Protocol):
    """Somewhere we can read recent headlines from (RSS feed, API, ...)."""

    name: str

    async def fetch(self) -> list[NewsItem]: ...
