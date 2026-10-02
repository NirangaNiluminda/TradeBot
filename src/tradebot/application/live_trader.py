"""Runs the trading engine on live data (testnet or real).

Three loops run side by side:
  - candles: every few seconds, look for newly CLOSED candles -> engine.on_candle
  - exits:   check whether a stop-loss / take-profit filled; refresh prices
  - news:    every N minutes, refresh the news/LLM risk level

A problem in one cycle (network blip, API error) is logged and retried on the
next cycle — it never crashes the bot. The loops end when the bot is STOPPED.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from tradebot.application.bot_control import BotControl
from tradebot.application.indicator_feed import RollingFeed
from tradebot.application.news_risk_service import NewsRiskService
from tradebot.application.trading_engine import TradingEngine
from tradebot.domain.models import Candle, ExitReason
from tradebot.domain.ports import MarketDataPort
from tradebot.domain.strategies import StrategyFactory
from tradebot.domain.timeframes import timeframe_delta

log = logging.getLogger(__name__)


class LiveTrader:
    def __init__(
        self,
        engine: TradingEngine,
        feed: RollingFeed,
        market_data: MarketDataPort,
        symbols: list[str],
        timeframe: str,
        control: BotControl,
        news: NewsRiskService | None = None,
        news_refresh_minutes: float = 20,
        poll_seconds: float = 20,
        history_candles: int = 1000,
        strategy_factory: StrategyFactory | None = None,
    ) -> None:
        self.engine, self.feed, self.market_data = engine, feed, market_data
        self.symbols, self.timeframe, self.control = symbols, timeframe, control
        self.news = news
        self.news_interval = news_refresh_minutes * 60
        self.poll = poll_seconds
        self.history_candles = history_candles
        self.strategy_factory = strategy_factory
        self._last_candle: dict[str, datetime] = {}
        self.started_at: datetime | None = None

    async def start(self) -> None:
        """Load recent history so indicators are ready, and pick up where we left off."""
        await self._load_history()
        await self.engine.sync_exits()  # exits that filled while the bot was off
        if self.news:
            await self.news.refresh()
        self.started_at = datetime.now(UTC)

    async def run(self) -> None:
        if self.started_at is None:
            await self.start()
        loops = [
            self._every(self.poll, self._check_candles),
            self._every(self.poll, self._check_exits_and_prices),
        ]
        if self.news:  # start() already read the news once
            loops.append(self._every(self.news_interval, self.news.refresh, first_wait=True))
        await asyncio.gather(*loops)
        # Loops only end when the bot was STOPPED (kill switch): close everything.
        await self.engine.close_all(ExitReason.MANUAL, datetime.now(UTC))

    async def _every(
        self, seconds: float, job: Callable[[], Awaitable[object]], first_wait: bool = False
    ) -> None:
        if first_wait:
            await self._sleep(seconds)
        while self.control.state.keeps_running:
            try:
                await job()
            except Exception:
                log.exception("Error in %s (will retry)", getattr(job, "__name__", job))
            await self._sleep(seconds)

    async def _sleep(self, seconds: float) -> None:
        """Sleep, but wake up quickly if the bot is stopped."""
        end = asyncio.get_running_loop().time() + seconds
        while self.control.state.keeps_running and asyncio.get_running_loop().time() < end:
            await asyncio.sleep(min(1.0, seconds))

    async def _load_history(self) -> None:
        now = datetime.now(UTC)
        step = self.engine.timeframe_delta
        for symbol in self.symbols:
            candles = await self.market_data.fetch_candles(
                symbol, self.timeframe, now - step * self.history_candles, now
            )
            if not candles:
                raise RuntimeError(f"No candles for {symbol}")
            self.feed.seed(symbol, candles)
            self._last_candle[symbol] = candles[-1].time
            self.engine.last_price[symbol] = candles[-1].close

    def recent_candles(self, symbol: str) -> list[Candle]:
        return self.feed.candles(symbol)

    async def reconfigure(
        self, strategy: str | None = None, params: dict | None = None, timeframe: str | None = None
    ) -> None:
        """Switch strategy and/or timeframe while running. Open positions keep their exits."""
        if self.strategy_factory is None:
            raise ValueError("runtime switching is not enabled")
        new_strategy = self.engine.strategy
        if strategy:
            new_strategy = self.strategy_factory.create(strategy, params or {})
        new_tf = timeframe or self.timeframe
        timeframe_delta(new_tf)  # validates, raises ValueError on nonsense
        feed = RollingFeed(new_strategy)
        old = (self.feed, self.timeframe, self.engine.timeframe_delta)
        self.feed, self.timeframe = feed, new_tf
        self.engine.timeframe_delta = timeframe_delta(new_tf)
        self.history_candles = max(1000, new_strategy.warmup * 5)
        try:
            await self._load_history()
        except Exception:
            self.feed, self.timeframe, self.engine.timeframe_delta = old
            raise
        self.engine.strategy, self.engine.feed = new_strategy, feed

    async def _check_candles(self) -> None:
        now = datetime.now(UTC)
        step = self.engine.timeframe_delta
        for symbol in self.symbols:
            new = await self.market_data.fetch_candles(
                symbol, self.timeframe, self._last_candle[symbol] + step, now
            )
            for candle in new:
                await self.engine.on_candle(symbol, candle)
                self._last_candle[symbol] = candle.time

    async def _check_exits_and_prices(self) -> None:
        for symbol in self.symbols:
            self.engine.last_price[symbol] = await self.market_data.fetch_last_price(symbol)
        await self.engine.sync_exits()
