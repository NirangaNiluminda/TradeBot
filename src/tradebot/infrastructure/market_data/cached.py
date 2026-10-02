"""Caches candles and trading rules on disk (Decorator pattern).

Wraps any MarketDataPort. The first backtest downloads data from Binance;
later runs read it from `data/` instantly and only download new candles.
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from tradebot.domain.models import Candle, SymbolRules
from tradebot.domain.ports import MarketDataPort
from tradebot.domain.timeframes import timeframe_delta

log = logging.getLogger(__name__)


class CachedMarketData:
    def __init__(self, inner: MarketDataPort, cache_dir: Path) -> None:
        self.inner = inner
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    async def fetch_candles(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> list[Candle]:
        path = self.cache_dir / f"{symbol.replace('/', '-')}_{timeframe}.csv"
        cached = _load(path)
        step = timeframe_delta(timeframe)

        if not cached or start < cached[0].time:
            # Missing older history: download the whole range again (simple and safe)
            fresh = await self.inner.fetch_candles(symbol, timeframe, start, end)
            cached = _merge(cached, fresh)
            _save(path, cached)
        elif end > cached[-1].time + step:
            try:
                fresh = await self.inner.fetch_candles(
                    symbol, timeframe, cached[-1].time + step, end
                )
            except Exception as exc:  # network trouble: the cached history is still useful
                log.warning(
                    "Could not download new %s candles (%s); using cached data up to %s",
                    symbol,
                    type(exc).__name__,
                    f"{cached[-1].time:%Y-%m-%d %H:%M}",
                )
                fresh = []
            if fresh:
                cached = _merge(cached, fresh)
                _save(path, cached)

        return [c for c in cached if start <= c.time < end]

    async def fetch_symbol_rules(self, symbol: str) -> SymbolRules:
        path = self.cache_dir / "symbol_rules.json"
        stored = json.loads(path.read_text()) if path.exists() else {}
        try:
            rules = await self.inner.fetch_symbol_rules(symbol)
        except Exception:
            if symbol in stored:  # offline: fall back to the last known rules
                return SymbolRules(**stored[symbol])
            raise
        stored[symbol] = asdict(rules)
        path.write_text(json.dumps(stored, indent=2))
        return rules


def _merge(old: list[Candle], new: list[Candle]) -> list[Candle]:
    by_time = {c.time: c for c in old}
    by_time.update({c.time: c for c in new})
    return [by_time[t] for t in sorted(by_time)]


def _load(path: Path) -> list[Candle]:
    if not path.exists():
        return []
    with path.open() as f:
        return [
            Candle(
                datetime.fromisoformat(r["time"]).astimezone(UTC),
                float(r["open"]),
                float(r["high"]),
                float(r["low"]),
                float(r["close"]),
                float(r["volume"]),
            )
            for r in csv.DictReader(f)
        ]


def _save(path: Path, candles: list[Candle]) -> None:
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "open", "high", "low", "close", "volume"])
        for c in candles:
            w.writerow([c.time.isoformat(), c.open, c.high, c.low, c.close, c.volume])
    tmp.replace(path)
