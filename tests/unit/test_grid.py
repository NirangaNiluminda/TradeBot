"""Grid bot: ladder layout, buy->sell cycles, stop-loss, re-centring, accounting."""

from datetime import UTC, datetime, timedelta

import pytest

from tradebot.application.grid_backtest import grid_candidates, run_grid, walk_forward
from tradebot.domain.grid import GridBook, GridConfig, SlotState
from tradebot.domain.models import Candle, SymbolRules

T0 = datetime(2026, 1, 1, tzinfo=UTC)
RULES = SymbolRules("XYZ/USDT", min_notional=5.0, amount_step=0.0001, min_amount=0.0001)


def c(i, low, high, close=None, open_=None):
    close = close if close is not None else (low + high) / 2
    return Candle(T0 + timedelta(minutes=i), open_ or close, high, low, close, 1.0)


def cfg(**kw):
    base = dict(budget=100.0, range_pct=4.0, levels=5, stop_loss_pct=5.0, fill_through_pct=0.0)
    return GridConfig(**(base | kw))


def test_ladder_has_buys_below_price_only():
    book = GridBook(cfg(), RULES)
    book.build(100.0, T0)
    assert book.lower == pytest.approx(96) and book.upper == pytest.approx(104)
    assert all((s.state is SlotState.BUY) == (s.buy_price < 100.0) for s in book.slots)
    assert {s.state for s in book.slots} == {SlotState.BUY, SlotState.IDLE}


def test_buy_then_sell_one_level_up_makes_a_profit():
    book = GridBook(cfg(fee_pct=0.1), RULES)
    book.on_candle(c(0, 100, 100))  # builds the grid
    slot = book.slots[1]
    book.on_candle(c(1, slot.buy_price - 0.01, 100))  # dips through the buy level
    assert slot.state is SlotState.SELL
    fills = book.on_candle(c(2, 99, slot.sell_price + 0.01))  # rises through the sell level
    sell = [f for f in fills if f.side == "sell"][0]
    assert sell.profit > 0 and slot.state is SlotState.BUY
    # ~2% step minus 0.2% fees
    assert sell.profit / (slot.cost or 25) < 0.02


def test_stop_loss_sells_everything_and_pauses():
    book = GridBook(cfg(stop_loss_pct=2.0, cooldown_minutes=60), RULES)
    book.on_candle(c(0, 100, 100))
    book.on_candle(c(1, 95.5, 99, close=96.5))  # buys both lower slots
    assert book.held_amount() > 0
    fills = book.on_candle(c(2, 92, 96, close=93))  # far below the ladder
    assert {f.side for f in fills} == {"stop"} and not book.holding and not book.active
    assert book.stopouts == 1
    book.on_candle(c(30, 92, 93))
    assert not book.active  # still cooling down
    book.on_candle(c(70, 92, 93))
    assert book.active and book.rebuilds == 2  # rebuilt around the new price


def test_recentres_when_price_leaves_range_upwards():
    book = GridBook(cfg(recenter_minutes=10), RULES)
    book.on_candle(c(0, 100, 100))
    for i in range(1, 15):
        book.on_candle(c(i, 110, 111, close=110.5))
    assert book.rebuilds == 2 and book.lower > 100


def test_money_adds_up():
    import math

    candles = [c(i, 100 + 3 * math.sin(i / 30) - 0.5, 100 + 3 * math.sin(i / 30) + 0.5)
               for i in range(3000)]  # fmt: skip
    r = run_grid(candles, cfg(levels=8), RULES)
    assert r.cycles > 10
    realised = r.cycle_profit + r.stop_loss_total
    book_value_check = r.final_equity - 100.0
    # equity change = realised profits + value change of coins still held
    assert abs(book_value_check - realised) < 10


def test_walk_forward_runs_and_uses_unseen_data():
    import math

    candles = [c(i, 100 + 3 * math.sin(i / 60) - 0.4, 100 + 3 * math.sin(i / 60) + 0.4)
               for i in range(60 * 24 * 9)]  # 9 days  # fmt: skip
    wf = walk_forward(candles, RULES, cfg(), train_days=3, test_days=2)
    assert len(wf.folds) == 3
    for f in wf.folds:
        assert f.test.start >= f.test_start and f.chosen in grid_candidates(cfg())
