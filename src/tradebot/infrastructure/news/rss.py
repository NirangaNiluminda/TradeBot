"""RSS news feed adapter (NewsSourcePort). Free, no API key."""

from __future__ import annotations

import calendar
import html
import re
from datetime import UTC, datetime

import feedparser
import httpx

from tradebot.domain.models import NewsItem

_TAGS = re.compile(r"<[^>]+>")


class RssNewsSource:
    def __init__(self, name: str, url: str, http: httpx.AsyncClient, max_items: int = 30) -> None:
        self.name, self.url, self._http, self.max_items = name, url, http, max_items

    async def fetch(self) -> list[NewsItem]:
        r = await self._http.get(self.url, headers={"User-Agent": "Mozilla/5.0 tradebot"})
        r.raise_for_status()
        return parse_feed(r.content, self.name)[: self.max_items]


def parse_feed(content: bytes | str, source: str) -> list[NewsItem]:
    feed = feedparser.parse(content)
    items = []
    for e in feed.entries:
        stamp = e.get("published_parsed") or e.get("updated_parsed")
        if not stamp or not e.get("title"):
            continue
        items.append(
            NewsItem(
                title=_clean(e.title),
                source=source,
                published=datetime.fromtimestamp(calendar.timegm(stamp), tz=UTC),
                summary=_clean(e.get("summary", ""))[:300],
                url=e.get("link", ""),
            )
        )
    return items


def _clean(text: str) -> str:
    return " ".join(html.unescape(_TAGS.sub(" ", text)).split())
