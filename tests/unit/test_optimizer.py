from datetime import timedelta

from tests.conftest import T0
from tradebot.application.backtest import BacktestResult
from tradebot.application.optimizer import (
    BacktestJob,
    WalkForwardOptimizer,
    build_candidates,
    expand_grid,
)
from tradebot.domain.strategies import strategy_factory


def test_expand_grid():
    assert expand_grid({"a": [1, 2], "b": ["x"]}) == [{"a": 1, "b": "x"}, {"a": 2, "b": "x"}]
    assert expand_grid({}) == [{}]


def test_candidates_skip_invalid_combinations():
    cands = build_candidates("ema_cross", strategy_factory)
    assert cands
    assert all(c.params_dict["fast"] < c.params_dict["slow"] for c in cands)
    assert all(c.risk_dict["exit_mode"] == "atr" for c in cands)


class RecordingBatch:
    """Fake batch: 'stop' setting 3.5 is great in training; records which periods were used."""

    def __init__(self):
        self.jobs: list[BacktestJob] = []

    def run_many(self, jobs):
        self.jobs.extend(jobs)
        out = []
        for j in jobs:
            r = BacktestResult("s", {}, ["X"], "1h", j.start, j.end, 10.0)
            good = j.candidate.risk_dict["stop_atr_mult"] == 3.5
            import pandas as pd

            r.equity_curve = pd.Series([10.0, 12.0 if good else 9.0])
            out.append(r)
        return out


def test_walk_forward_never_tests_on_training_data():
    batch = RecordingBatch()
    opt = WalkForwardOptimizer(batch, train_days=100, test_days=30, min_trades_per_year=0)
    cands = build_candidates("regime_switch", strategy_factory)
    result = opt.run(cands, T0, T0 + timedelta(days=200))

    assert len(result.folds) == 3  # (200 - 100) // 30
    for f in result.folds:
        assert f.best.risk_dict["stop_atr_mult"] == 3.5  # best training setting was chosen
        assert f.test_start == f.train_start + timedelta(days=100)
        # the test job ran only on the unseen period
        test_jobs = [j for j in batch.jobs if j.start == f.test_start and j.end == f.test_end]
        assert len(test_jobs) == 1
    assert result.recommended is not None
