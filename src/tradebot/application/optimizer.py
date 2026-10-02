"""Walk-forward optimisation — the honest way to tune a strategy.

    |---- train ----|-- test --|
              |---- train ----|-- test --|
                        |---- train ----|-- test --|

For each step: try every setting on the TRAIN period, pick the best, then
measure that setting on the following TEST period it has never seen.
Only the test results count. If a strategy only looked good because its
settings were fitted to the past, it shows up here as poor test results.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

from tradebot.application.backtest import BacktestResult
from tradebot.application.metrics import PerformanceReport, evaluate
from tradebot.domain.models import Trade
from tradebot.domain.strategies.base import StrategyFactory

# Stop-loss / take-profit settings tried together with every strategy setting.
EXIT_GRID: dict[str, list[Any]] = {"stop_atr_mult": [1.5, 2.5, 3.5], "reward_ratio": [1.5, 2.5]}


@dataclass(frozen=True)
class Candidate:
    """One combination of settings to try."""

    strategy: str
    params: tuple[tuple[str, Any], ...]  # strategy parameters (tuple so it's hashable)
    risk: tuple[tuple[str, Any], ...]  # overrides for RiskConfig

    @property
    def params_dict(self) -> dict[str, Any]:
        return dict(self.params)

    @property
    def risk_dict(self) -> dict[str, Any]:
        return dict(self.risk)

    def label(self) -> str:
        parts = [f"{k}={v}" for k, v in self.params]
        r = self.risk_dict
        parts.append(f"stop={r['stop_atr_mult']}xATR")
        parts.append(f"target={r['take_profit_atr_mult']}xATR")
        return " ".join(parts)


@dataclass(frozen=True)
class BacktestJob:
    candidate: Candidate
    start: datetime
    end: datetime


class BacktestBatch(Protocol):
    """Runs many backtests (maybe in parallel). Injected by the composition root."""

    def run_many(self, jobs: list[BacktestJob]) -> list[BacktestResult]: ...


@dataclass
class FoldResult:
    train_start: datetime
    test_start: datetime
    test_end: datetime
    best: Candidate
    train: PerformanceReport
    test: PerformanceReport
    test_trades: list[Trade]
    test_buy_and_hold_pct: float


@dataclass
class WalkForwardResult:
    strategy: str
    candidates_per_fold: int
    folds: list[FoldResult] = field(default_factory=list)
    recommended: Candidate | None = None  # best on the most recent training window
    recommended_train: PerformanceReport | None = None

    @property
    def test_return_pct(self) -> float:
        """Compounded return over all test periods."""
        return (math.prod(1 + f.test.total_return_pct / 100 for f in self.folds) - 1) * 100

    @property
    def buy_and_hold_pct(self) -> float:
        return (math.prod(1 + f.test_buy_and_hold_pct / 100 for f in self.folds) - 1) * 100

    @property
    def test_trades(self) -> list[Trade]:
        return [t for f in self.folds for t in f.test_trades]

    @property
    def test_profit_factor(self) -> float:
        won = sum(t.pnl for t in self.test_trades if t.pnl > 0)
        lost = -sum(t.pnl for t in self.test_trades if t.pnl <= 0)
        return won / lost if lost else (math.inf if won else 0.0)

    @property
    def worst_test_drawdown_pct(self) -> float:
        return max((f.test.max_drawdown_pct for f in self.folds), default=0.0)

    @property
    def profitable_folds(self) -> int:
        return sum(f.test.total_return_pct > 0 for f in self.folds)


def score(report: PerformanceReport, min_trades: int) -> float:
    """How good a training result is: profit minus half the worst drop.

    Needs a minimum number of trades — a few lucky trades are not evidence.
    """
    if report.trades < min_trades:
        return -math.inf
    return report.total_return_pct - 0.5 * report.max_drawdown_pct


def expand_grid(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    if not grid:
        return [{}]
    keys = list(grid)
    return [dict(zip(keys, values, strict=True)) for values in itertools.product(*grid.values())]


def build_candidates(strategy: str, factory: StrategyFactory) -> list[Candidate]:
    """All valid combinations of the strategy's param_grid and EXIT_GRID."""
    cls = factory.available()[strategy]
    out = []
    for params in expand_grid(cls.param_grid):
        try:
            factory.create(strategy, dict(params))  # skip invalid combos (e.g. fast >= slow)
        except ValueError:
            continue
        for exits in expand_grid(EXIT_GRID):
            risk = {
                "exit_mode": "atr",
                "stop_atr_mult": exits["stop_atr_mult"],
                "take_profit_atr_mult": round(exits["stop_atr_mult"] * exits["reward_ratio"], 2),
            }
            out.append(Candidate(strategy, tuple(params.items()), tuple(risk.items())))
    return out


class WalkForwardOptimizer:
    def __init__(
        self,
        batch: BacktestBatch,
        train_days: int = 365,
        test_days: int = 90,
        min_trades_per_year: int = 8,
        on_progress: Callable[[str], None] = lambda _: None,
    ) -> None:
        self.batch = batch
        self.train = timedelta(days=train_days)
        self.test = timedelta(days=test_days)
        self.min_trades = max(3, round(min_trades_per_year * train_days / 365))
        self.on_progress = on_progress

    def _pick_best(
        self, candidates: list[Candidate], start: datetime, end: datetime
    ) -> tuple[Candidate, PerformanceReport]:
        results = self.batch.run_many([BacktestJob(c, start, end) for c in candidates])
        scored = [(score(evaluate(r), self.min_trades), i) for i, r in enumerate(results)]
        _, best_i = max(scored)  # ties -> the later candidate; deterministic
        return candidates[best_i], evaluate(results[best_i])

    def run(self, candidates: list[Candidate], start: datetime, end: datetime) -> WalkForwardResult:
        result = WalkForwardResult(candidates[0].strategy, len(candidates))
        n_folds = max(0, int((end - start - self.train) / self.test))
        for k in range(n_folds):
            train_start = start + k * self.test
            test_start = train_start + self.train
            test_end = test_start + self.test
            self.on_progress(f"{result.strategy}: step {k + 1}/{n_folds}")
            best, train_report = self._pick_best(candidates, train_start, test_start)
            [test_run] = self.batch.run_many([BacktestJob(best, test_start, test_end)])
            bh = test_run.buy_and_hold_pct
            result.folds.append(
                FoldResult(
                    train_start=train_start,
                    test_start=test_start,
                    test_end=test_end,
                    best=best,
                    train=train_report,
                    test=evaluate(test_run),
                    test_trades=test_run.trades,
                    test_buy_and_hold_pct=sum(bh.values()) / len(bh) if bh else 0.0,
                )
            )
        # Settings to use from now on: best on the most recent training window.
        self.on_progress(f"{result.strategy}: choosing settings for today")
        result.recommended, result.recommended_train = self._pick_best(
            candidates, end - self.train, end
        )
        return result
