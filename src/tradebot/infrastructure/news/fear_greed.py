"""Crypto Fear & Greed Index (alternative.me) as a news item. Free, no API key.

0-24 extreme fear, 25-49 fear, 50-74 greed, 75-100 extreme greed.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from tradebot.domain.models import NewsItem

_URL = "https://api.alternative.me/fng/?limit=2"


class FearGreedSource:
    name = "fear_greed"

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def fetch(self) -> list[NewsItem]:
        r = await self._http.get(_URL)
        r.raise_for_status()
        today, *rest = r.json()["data"]
        change = ""
        if rest:
            change = f" (yesterday {rest[0]['value']})"
        return [
            NewsItem(
                title=(
                    f"Crypto Fear & Greed Index: {today['value']} "
                    f"({today['value_classification']}){change}"
                ),
                source="alternative.me",
                published=datetime.fromtimestamp(int(today["timestamp"]), tz=UTC),
            )
        ]
