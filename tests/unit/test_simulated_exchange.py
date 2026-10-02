from datetime import timedelta

import pytest

from tests.conftest import candle
from tradebot.domain.models import ExitReason
from tradebot.domain.ports import OrderRejected
from tradebot.infrastructure.exchange.simulated import SimulatedExchange

SYM = "XYZ/USDT"


def make(rules, quote=10.0, fee=0.1, slip=0.0):
    ex = SimulatedExchange({SYM: rules}, quote, timedelta(hours=1), fee_pct=fee, slippage_pct=slip)
    ex.on_candle(SYM, candle(0, 100.0))
    return ex


async def test_buy_rounds_amount_and_charges_fee(unit_rules):
    ex = make(unit_rules)
    fill = await ex.market_buy(SYM, 9.5)
    assert fill.amount == 0.095
    assert fill.fee == pytest.approx(0.0095)
    assert ex.balances["USDT"] == pytest.approx(10 - 9.5 - 0.0095)
    assert ex.balances["XYZ"] == pytest.approx(0.095)


async def test_order_below_minimum_is_rejected(unit_rules):
    ex = make(unit_rules)
    with pytest.raises(OrderRejected):
        await ex.market_buy(SYM, 4.0)


async def test_stop_loss_wins_when_both_exits_touched(unit_rules):
    ex = make(unit_rules)
    fill = await ex.market_buy(SYM, 9.5)
    await ex.place_exit_orders(SYM, fill.amount, stop_loss=98, take_profit=104)
    ex.on_candle(SYM, candle(1, 100, open_=100, high=105, low=97))
    [exit_fill] = await ex.fetch_exit_fills()
    assert exit_fill.reason is ExitReason.STOP_LOSS
    assert exit_fill.price == 98


async def test_gap_below_stop_fills_at_open(unit_rules):
    ex = make(unit_rules)
    fill = await ex.market_buy(SYM, 9.5)
    await ex.place_exit_orders(SYM, fill.amount, stop_loss=98, take_profit=104)
    ex.on_candle(SYM, candle(1, 94, open_=95, high=96, low=93))
    [exit_fill] = await ex.fetch_exit_fills()
    assert exit_fill.price == 95  # worse than the stop price, like in real life


async def test_take_profit(unit_rules):
    ex = make(unit_rules)
    fill = await ex.market_buy(SYM, 9.5)
    await ex.place_exit_orders(SYM, fill.amount, stop_loss=98, take_profit=104)
    ex.on_candle(SYM, candle(1, 103, open_=101, high=104.5, low=100.5))
    [exit_fill] = await ex.fetch_exit_fills()
    assert exit_fill.reason is ExitReason.TAKE_PROFIT and exit_fill.price == 104
    assert ex.balances["XYZ"] == pytest.approx(0)


async def test_coins_locked_in_exit_orders_cannot_be_sold_twice(unit_rules):
    ex = make(unit_rules)
    fill = await ex.market_buy(SYM, 9.5)
    await ex.place_exit_orders(SYM, fill.amount, 98, 104)
    with pytest.raises(OrderRejected):
        await ex.market_sell(SYM, fill.amount)
    await ex.cancel_exit_orders(SYM)
    await ex.market_sell(SYM, fill.amount)
