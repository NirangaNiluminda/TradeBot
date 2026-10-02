"""The most important guarantee: strategies never peek at future candles."""

import pytest

from tests.conftest import random_walk
from tradebot.application.frames import candles_to_frame
from tradebot.application.indicator_feed import SIGNAL_LOOKBACK
from tradebot.domain.models import Action
from tradebot.domain.strategies import StrategyContext, strategy_factory
from tradebot.domain.strategies.frame import Frame


@pytest.mark.parametrize("name", list(strategy_factory.available()))
def test_signal_identical_with_and_without_future_data(name):
    """Indicators computed on ALL data vs. only on data up to candle i must give the same signal."""
    strategy = strategy_factory.create(name)
    df = candles_to_frame(random_walk(900))
    full = Frame.from_dataframe(strategy.indicators(df))
    actions = set()
    for i in range(strategy.warmup, len(df), 7):
        ctx = StrategyContext(in_position=bool(i % 2))
        past_only = Frame.from_dataframe(strategy.indicators(df.iloc[: i + 1]))
        a = strategy.signal(full.slice(max(0, i + 1 - SIGNAL_LOOKBACK), i + 1), ctx)
        b = strategy.signal(past_only.tail(SIGNAL_LOOKBACK), ctx)
        assert a == b, f"{name} differs at candle {i}"
        actions.add(a.action)
    assert actions  # sanity: we actually checked something


@pytest.mark.parametrize("name", list(strategy_factory.available()))
def test_every_strategy_trades_on_random_data(name):
    # Random data has no real volume spikes or trends, so members of the default
    # ensemble rarely agree; with 1 vote required we still test the voting machinery.
    strategy = strategy_factory.create(name, {"min_votes": 1} if name == "ensemble" else None)
    full = Frame.from_dataframe(strategy.indicators(candles_to_frame(random_walk(3000, seed=3))))
    actions = {
        strategy.signal(full.slice(max(0, i - SIGNAL_LOOKBACK), i + 1), ctx).action
        for i in range(strategy.warmup, len(full))
        for ctx in (StrategyContext(in_position=False), StrategyContext(in_position=True))
    }
    assert Action.BUY in actions and Action.SELL in actions


def test_factory_builds_nested_config():
    s = strategy_factory.create(
        "regime_switch",
        {"threshold": 30, "trend": {"name": "ema_cross", "params": {"fast": 5, "slow": 10}}},
    )
    assert s.params()["threshold"] == 30
    assert "fast=5" in s.params()["trend"]


def test_unknown_strategy_lists_available_names():
    with pytest.raises(ValueError, match="ema_cross"):
        strategy_factory.create("does_not_exist")


def test_frame_slicing_and_groups():
    import pandas as pd

    df = pd.DataFrame({("a", "x"): [1.0, 2.0, 3.0], ("b", "x"): [4.0, 5.0, 6.0]})
    f = Frame.from_dataframe(df)
    assert list(f.group("a")["x"]) == [1.0, 2.0, 3.0]
    assert list(f.slice(1, 3).group("b")["x"]) == [5.0, 6.0]
    assert len(f.head(2)) == 2 and f.tail(1).group("a")["x"][-1] == 3.0


def test_late_entry_never_looks_ahead_and_catches_recent_signal():
    from tradebot.domain.models import Action

    plain = strategy_factory.create("breakout", {"volume_mult": 1.0})
    late = strategy_factory.create(
        "breakout", {"volume_mult": 1.0, "late_entry_window": 3, "late_entry_max_atr": 5.0}
    )
    df = candles_to_frame(random_walk(900, seed=11))
    full = Frame.from_dataframe(late.indicators(df))
    plain_full = Frame.from_dataframe(plain.indicators(df))
    flat = StrategyContext(in_position=False)
    extra = 0
    for i in range(late.warmup, len(df)):
        a = late.signal(full.slice(max(0, i + 1 - SIGNAL_LOOKBACK), i + 1), flat)
        if i % 9 == 0:  # no look-ahead: same answer from data up to i only
            past = Frame.from_dataframe(late.indicators(df.iloc[: i + 1])).tail(SIGNAL_LOOKBACK)
            assert late.signal(past, flat) == a
        p = plain.signal(plain_full.slice(max(0, i + 1 - SIGNAL_LOOKBACK), i + 1), flat)
        if a.action is Action.BUY and p.action is not Action.BUY:
            assert "late entry" in a.reason
            extra += 1
    assert extra > 0  # it does catch signals the plain strategy only saw earlier
