"""Paper trading: simulated orders checked against (fake here) real 1-minute prices."""

from datetime import UTC, datetime, timedelta

import pytest

from tradebot.domain.models import Candle, ExitReason, Position
from tradebot.infrastructure.exchange.paper import PaperExchange

SYM = "XYZ/USDT"
MIN = timedelta(minutes=1)


class FakeMarket:
    """Pretends to be Binance: 1-minute candles + a live price."""

    def __init__(self):
        self.candles: list[Candle] = []
        self.price = 100.0

    def add(self, t: datetime, low: float, high: float, close: float) -> None:
        self.candles.append(Candle(t, close, high, low, close, 10.0))
        self.price = close

    async def fetch_candles(self, symbol, timeframe, start, end):
        return [c for c in self.candles if start <= c.time < end]

    async def fetch_last_price(self, symbol):
        return self.price


@pytest.fixture
def market():
    return FakeMarket()


def now_minute():
    return datetime.now(UTC).replace(second=0, microsecond=0)


async def test_buy_uses_live_price_and_stop_fires_on_real_low(market, unit_rules):
    ex = PaperExchange(market, {SYM: unit_rules}, starting_quote=10.0, slippage_pct=0)
    market.price = 100.0
    fill = await ex.market_buy(SYM, 9.5)
    assert fill.price == 100.0
    await ex.place_exit_orders(SYM, fill.amount, stop_loss=98.0, take_profit=104.0)
    assert await ex.fetch_exit_fills() == []

    market.add(now_minute() - MIN, low=97.5, high=100.5, close=99.0)  # dips through the stop
    [exit_fill] = await ex.fetch_exit_fills()
    assert exit_fill.reason is ExitReason.STOP_LOSS and exit_fill.price == 98.0


async def test_restart_replays_prices_since_the_buy(market, unit_rules):
    entry = now_minute() - 30 * MIN
    market.add(entry + 5 * MIN, low=103.0, high=105.0, close=104.5)  # target hit while bot was off
    ex = PaperExchange(market, {SYM: unit_rules}, starting_quote=0.5, slippage_pct=0)
    pos = Position(SYM, 0.095, 100.0, entry, 0.0, 98.0, 104.0, "breakout", "sim")
    await ex.restore_exit_orders(pos)
    [exit_fill] = await ex.fetch_exit_fills()
    assert exit_fill.reason is ExitReason.TAKE_PROFIT
    assert ex.balances["USDT"] > 0.5 + 0.095 * 104.0 * 0.99  # sale proceeds credited
