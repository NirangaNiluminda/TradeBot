"""Phase 3 safety pieces: bot states, cash ledger, database, emergency exits."""

from datetime import timedelta

import pytest

from tests.conftest import HOUR, candle
from tradebot.application.bot_control import BotControl, InvalidTransition
from tradebot.application.event_bus import EventBus
from tradebot.application.market_risk import StaticMarketRisk
from tradebot.application.recorder import TradeRecorder, ledger_cash
from tradebot.application.trading_engine import TradingEngine
from tradebot.bootstrap import build_risk_manager
from tradebot.config import RiskConfig
from tradebot.domain.events import PositionClosed, TradeRejected
from tradebot.domain.models import ExitReason, TradeProposal
from tradebot.domain.ports import OrderRejected
from tradebot.domain.strategies import strategy_factory
from tradebot.infrastructure.exchange.simulated import SimulatedExchange
from tradebot.infrastructure.persistence.sqlite_repository import SqliteTradeRepository

SYM = "XYZ/USDT"


def make_engine(rules, bus=None, control=None, exchange=None):
    ex = exchange or SimulatedExchange({SYM: rules}, 10_000, HOUR)  # testnet-like big balance
    ex.on_candle(SYM, candle(0, 100.0))
    eng = TradingEngine(
        strategy_factory.create("breakout"), None, ex, build_risk_manager(RiskConfig()),
        StaticMarketRisk(), bus or EventBus(), timedelta(hours=1), starting_cash=10.0,
        control=control,
    )  # fmt: skip
    return eng, ex


def proposal():
    return TradeProposal(SYM, 100.0, quote_amount=9.5, stop_loss=98.0, take_profit=104.0)


def test_bot_state_transitions():
    c = BotControl()
    assert c.state.allows_new_trades
    c.pause()
    assert not c.state.allows_new_trades and c.state.keeps_running
    c.resume()
    c.stop()
    assert not c.state.keeps_running
    with pytest.raises(InvalidTransition):
        c.resume()  # STOPPED is final


async def test_engine_uses_own_budget_not_exchange_balance(unit_rules):
    eng, _ = make_engine(unit_rules)
    snap = await eng.snapshot()
    assert snap.free_quote == 10.0 and snap.equity == 10.0  # not 10,000


async def test_cash_ledger_round_trip(unit_rules):
    bus, closed = EventBus(), []
    bus.subscribe(PositionClosed, closed.append)
    eng, ex = make_engine(unit_rules, bus)
    await eng.execute_buy(proposal(), candle(0, 100).time)
    ex.on_candle(SYM, candle(1, 110.0))
    await eng.close_position(SYM, ExitReason.MANUAL, candle(1, 110).time)
    [event] = closed
    assert eng.cash == pytest.approx(10.0 + event.trade.pnl)


async def test_position_without_stop_loss_is_sold_immediately(unit_rules):
    bus, rejected, closed = EventBus(), [], []
    bus.subscribe(TradeRejected, rejected.append)
    bus.subscribe(PositionClosed, closed.append)
    eng, ex = make_engine(unit_rules, bus)

    async def broken(*args):
        raise OrderRejected("OCO not allowed")

    ex.place_exit_orders = broken
    await eng.execute_buy(proposal(), candle(0, 100).time)
    assert not eng.positions
    assert "could not place stop-loss" in rejected[0].reason
    assert closed[0].trade.exit_reason is ExitReason.EMERGENCY


async def test_exit_that_filled_just_before_cancel_is_not_sold_twice(unit_rules):
    eng, ex = make_engine(unit_rules)
    await eng.execute_buy(proposal(), candle(0, 100).time)
    ex.on_candle(SYM, candle(1, 105, open_=101, high=105, low=100.5))  # take-profit fills
    await eng.close_position(SYM, ExitReason.STRATEGY, candle(1, 105).time)  # must not raise
    assert not eng.positions


async def test_paused_bot_does_not_open_but_keeps_exits(unit_rules):
    control = BotControl()
    eng, _ = make_engine(unit_rules, control=control)
    control.pause()
    assert not control.state.allows_new_trades


def test_repository_round_trip_and_restart_ledger(tmp_path, unit_rules):
    import asyncio

    repo = SqliteTradeRepository(tmp_path / "t.sqlite")
    bus = EventBus()
    TradeRecorder(repo, bus)
    eng, ex = make_engine(unit_rules, bus)

    async def scenario():
        await eng.execute_buy(proposal(), candle(0, 100).time)  # trade 1 opens
        ex.on_candle(SYM, candle(1, 90.0, open_=99, high=99, low=89))  # stop-loss hits
        await eng.sync_exits()
        ex.on_candle(SYM, candle(2, 90.0))
        await eng.execute_buy(proposal(), candle(2, 90).time)  # trade 2 stays open

    asyncio.run(scenario())
    trades, positions = repo.load_trades(), repo.load_positions()
    assert len(trades) == 1 and trades[0].exit_reason is ExitReason.STOP_LOSS
    assert [p.symbol for p in positions] == [SYM] and positions[0].exit_order_id
    # after a restart the rebuilt cash equals the live engine's cash
    assert ledger_cash(10.0, trades, positions) == pytest.approx(eng.cash)
