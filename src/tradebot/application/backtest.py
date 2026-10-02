"""Backtesting: replay past candles through the real trading engine."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from itertools import groupby
from typing import Protocol

import pandas as pd

from tradebot.application.event_bus import EventBus
from tradebot.application.trading_engine import TradingEngine
from tradebot.domain.events import PositionClosed, TradeRejected
from tradebot.domain.models import Candle, ExitReason, Trade
from tradebot.domain.ports import ExchangePort


class MarketSimulator(ExchangePort, Protocol):
    """An exchange that is driven by candles we feed it (used only for backtests)."""

    def on_candle(self, symbol: str, candle: Candle) -> None: ...


@dataclass
class BacktestResult:
    strategy: str
    params: dict
    symbols: list[str]
    timeframe: str
    start: datetime
    end: datetime
    starting_balance: float
    trades: list[Trade] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    buy_and_hold_pct: dict[str, float] = field(default_factory=dict)
    rejections: Counter[str] = field(default_factory=Counter)


class BacktestRunner:
    def __init__(self, engine: TradingEngine, simulator: MarketSimulator, bus: EventBus) -> None:
        self.engine, self.simulator, self.bus = engine, simulator, bus

    async def run(
        self,
        candles: dict[str, list[Candle]],
        start: datetime,
        starting_balance: float,
        timeframe: str,
        end: datetime | None = None,
    ) -> BacktestResult:
        """Trade candles in [start, end). Earlier candles are only used to warm up indicators."""

        def in_period(c: Candle) -> bool:
            return c.time >= start and (end is None or c.time < end)

        result = BacktestResult(
            strategy=self.engine.strategy.name,
            params=self.engine.strategy.params(),
            symbols=list(candles),
            timeframe=timeframe,
            start=start,
            end=start,
            starting_balance=starting_balance,
        )
        # Observer pattern: the report listens to the engine's events.
        self.bus.subscribe(PositionClosed, lambda e: result.trades.append(e.trade))
        self.bus.subscribe(TradeRejected, lambda e: result.rejections.update([_short(e.reason)]))

        stream = sorted(
            (
                (c.time, symbol, c)
                for symbol, series in candles.items()
                for c in series
                if in_period(c)
            ),
            key=lambda x: (x[0], x[1]),
        )
        equity: dict[datetime, float] = {}
        now = start
        for time, group in groupby(stream, key=lambda x: x[0]):
            for _, symbol, candle in group:
                self.simulator.on_candle(symbol, candle)
                await self.engine.on_candle(symbol, candle)
            now = time + self.engine.timeframe_delta
            equity[now] = await self.engine.equity()

        await self.engine.close_all(ExitReason.END_OF_TEST, now)
        if equity:
            equity[now] = await self.engine.equity()

        result.end = now
        result.equity_curve = pd.Series(equity, dtype=float)
        for symbol, series in candles.items():
            traded = [c for c in series if in_period(c)]
            if traded:
                result.buy_and_hold_pct[symbol] = (traded[-1].close / traded[0].close - 1) * 100
        return result


def _short(reason: str) -> str:
    """Group similar rejection reasons for the report (numbers replaced by N)."""
    return re.sub(r"\d+(\.\d+)?", "N", reason)
