import numpy as np
import pandas as pd
import pytest

from tradebot.research.trend_ensemble import (
    TrendConfig,
    donchian_signal,
    portfolio,
    simulate,
    target_weights,
)

IDX = pd.date_range("2020-01-01", periods=12, freq="D", tz="UTC")


def test_enters_on_new_high_and_exits_below_rising_stop():
    close = pd.Series([10, 11, 12, 13, 14, 15, 14, 13.4, 12, 11, 10, 9], index=IDX, dtype=float)
    sig = donchian_signal(close, 3)
    assert sig.iloc[2] == 1  # 12 is the highest close of the last 3 days -> enter
    # midpoint of the last 3 closes rises with the trend; the stop never moves down
    assert sig.iloc[5] == 1
    exit_day = int(np.argmax((sig.diff() == -1).to_numpy()))
    assert close.iloc[exit_day] < close.iloc[5]  # left only after the price turned down
    assert sig.iloc[-1] == 0


def test_no_look_ahead():
    rng = np.random.default_rng(5)
    idx = pd.date_range("2020-01-01", periods=600, freq="D", tz="UTC")
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.001, 0.03, 600))), index=idx)
    full, _ = target_weights(close, TrendConfig())
    for t in (150, 300, 450, 599):
        part, _ = target_weights(close.iloc[: t + 1], TrendConfig())
        assert part.iloc[-1] == pytest.approx(full.iloc[t])


def test_weights_never_exceed_spot_cap_and_fees_are_charged():
    rng = np.random.default_rng(7)
    idx = pd.date_range("2020-01-01", periods=800, freq="D", tz="UTC")
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.002, 0.02, 800))), index=idx)
    r = simulate(close, TrendConfig(target_vol=5.0))  # huge target -> would want leverage
    assert r.weight.max() <= 1.0 + 1e-9
    assert r.fees > 0 and r.trades > 0
    free = simulate(close, TrendConfig(target_vol=5.0, fee=0.0))
    assert free.equity.iloc[-1] > r.equity.iloc[-1]


def test_portfolio_averages_coins():
    rng = np.random.default_rng(9)
    idx = pd.date_range("2020-01-01", periods=500, freq="D", tz="UTC")
    a = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.001, 0.03, 500))), index=idx)
    b = pd.Series(50 * np.exp(np.cumsum(rng.normal(0.0, 0.03, 500))), index=idx)
    p = portfolio({"A": a, "B": b}, TrendConfig())
    assert len(p.equity) == 500 and p.equity.iloc[0] == pytest.approx(1.0)
