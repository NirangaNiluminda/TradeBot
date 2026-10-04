"""Backtest and walk-forward test for the grid bot (on 1-minute candles)."""

from __future__ import annotations

import itertools
import math
from bisect import bisect_left
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from tradebot.domain.grid import GridBook, GridConfig, GridFill, drawdown_pct
from tradebot.domain.models import Candle, SymbolRules


@dataclass
class GridResult:
    config: GridConfig
    start: datetime
    end: datetime
    fills: list[GridFill] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)  # sampled hourly
    final_equity: float = 0.0
    buy_and_hold_pct: float = 0.0
    rebuilds: int = 0
    stopouts: int = 0

    @property
    def return_pct(self) -> float:
        return (self.final_equity / self.config.budget - 1) * 100

    @property
    def cycles(self) -> int:
        """Completed buy -> sell round trips (the grid's 'trades')."""
        return sum(1 for f in self.fills if f.side == "sell")

    @property
    def stop_loss_total(self) -> float:
        return sum(f.profit for f in self.fills if f.side == "stop")

    @property
    def cycle_profit(self) -> float:
        return sum(f.profit for f in self.fills if f.side == "sell")

    @property
    def fees(self) -> float:
        return sum(f.fee for f in self.fills)

    @property
    def max_drawdown_pct(self) -> float:
        return drawdown_pct(self.equity)

    @property
    def trades_per_day(self) -> float:
        days = max((self.end - self.start).total_seconds() / 86400, 1e-9)
        return self.cycles / days


def run_grid(
    candles: list[Candle],
    config: GridConfig,
    rules: SymbolRules,
    start: datetime | None = None,
    end: datetime | None = None,
) -> GridResult:
    """Replay 1-minute candles in [start, end) through a fresh grid."""
    period = [c for c in candles if (start is None or c.time >= start) and
              (end is None or c.time < end)]  # fmt: skip
    if not period:
        raise ValueError("no candles in the requested period")
    book = GridBook(config, rules)
    result = GridResult(config, period[0].time, period[-1].time + timedelta(minutes=1))
    for i, candle in enumerate(period):
        result.fills.extend(book.on_candle(candle))
        if i % 60 == 0:
            result.equity.append(book.equity(candle.close))
    last = period[-1].close
    result.final_equity = book.equity(last)  # coins still held are valued at the last price
    result.equity.append(result.final_equity)
    result.buy_and_hold_pct = (last / period[0].open - 1) * 100
    result.rebuilds, result.stopouts = book.rebuilds, book.stopouts
    return result


# ---------------------------------------------------------------- walk-forward
GRID_SEARCH: dict[str, list] = {
    "range_pct": [2.0, 4.0, 8.0],
    "levels": [6, 10, 16],
    "stop_loss_pct": [None, 5.0],
}


def grid_candidates(base: GridConfig, search: dict[str, list] | None = None) -> list[GridConfig]:
    search = search or GRID_SEARCH
    keys = list(search)
    return [
        replace(base, **dict(zip(keys, values, strict=True)))
        for values in itertools.product(*search.values())
    ]


def score(r: GridResult) -> float:
    """Profit minus half the worst drop (same idea as the strategy optimizer)."""
    return r.return_pct - 0.5 * r.max_drawdown_pct


@dataclass
class GridFold:
    test_start: datetime
    test_end: datetime
    chosen: GridConfig
    train: GridResult
    test: GridResult


@dataclass
class GridWalkForward:
    folds: list[GridFold] = field(default_factory=list)

    @property
    def test_return_pct(self) -> float:
        return (math.prod(1 + f.test.return_pct / 100 for f in self.folds) - 1) * 100

    @property
    def buy_and_hold_pct(self) -> float:
        return (math.prod(1 + f.test.buy_and_hold_pct / 100 for f in self.folds) - 1) * 100

    @property
    def cycles(self) -> int:
        return sum(f.test.cycles for f in self.folds)

    @property
    def stopouts(self) -> int:
        return sum(f.test.stopouts for f in self.folds)

    @property
    def folds_won(self) -> int:
        return sum(f.test.return_pct > 0 for f in self.folds)

    @property
    def worst_drawdown_pct(self) -> float:
        return max((f.test.max_drawdown_pct for f in self.folds), default=0.0)

    @property
    def fees(self) -> float:
        return sum(f.test.fees for f in self.folds)

    @property
    def cycle_profit(self) -> float:
        return sum(f.test.cycle_profit for f in self.folds)

    @property
    def stop_loss_total(self) -> float:
        return sum(f.test.stop_loss_total for f in self.folds)


def walk_forward(
    candles: list[Candle],
    rules: SymbolRules,
    base: GridConfig,
    train_days: int = 60,
    test_days: int = 30,
    on_progress: Callable[[str], None] = lambda _: None,
) -> GridWalkForward:
    """Pick the best grid settings on `train_days`, then test on the next unseen `test_days`."""
    first, last = candles[0].time, candles[-1].time
    times = [c.time for c in candles]
    train, test = timedelta(days=train_days), timedelta(days=test_days)
    out = GridWalkForward()
    k = 0
    while first + k * test + train + test <= last + timedelta(minutes=1):
        t0 = first + k * test
        t1, t2 = t0 + train, t0 + train + test
        on_progress(f"step {k + 1}: training {t0:%Y-%m-%d} → {t1:%Y-%m-%d}")
        i0, i1, i2 = (bisect_left(times, t) for t in (t0, t1, t2))
        train_part, test_part = candles[i0:i1], candles[i1:i2]
        results = [run_grid(train_part, c, rules) for c in grid_candidates(base)]
        best = max(results, key=score)
        tested = run_grid(test_part, best.config, rules)
        out.folds.append(GridFold(t1, t2, best.config, best, tested))
        k += 1
    return out
