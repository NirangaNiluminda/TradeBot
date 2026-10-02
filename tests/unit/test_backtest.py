"""End-to-end: engine + simulator + risk rules, checking the money adds up."""

from typing import Any

import pandas as pd
import pytest

from tests.conftest import HOUR, T0, random_walk
from tradebot.bootstrap import run_backtest
from tradebot.config import AppConfig, MarketConfig
from tradebot.domain.models import Action, Signal
from tradebot.domain.strategies.base import Strategy, StrategyContext
from tradebot.domain.strategies.frame import Frame


class EveryNCandles(Strategy):
    """Test strategy: buy every 20 candles, sell 8 candles later."""

    name = "every_n"

    @property
    def warmup(self) -> int:
        return 1

    def indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["n"] = range(len(out))
        return out

    def signal(self, history: Frame, ctx: StrategyContext) -> Signal:
        n = history["n"][-1]
        if n % 20 == 0:
            return Signal(Action.BUY, "test buy")
        if n % 20 == 8:
            return Signal(Action.SELL, "test sell")
        return Signal.hold()

    def params(self) -> dict[str, Any]:
        return {}


async def test_final_equity_equals_start_plus_sum_of_trade_profits(unit_rules):
    cfg = AppConfig(market=MarketConfig(timeframe="1h"))  # test candles are hourly
    candles = {"XYZ/USDT": random_walk(1000)}
    result = await run_backtest(cfg, EveryNCandles(), candles, {"XYZ/USDT": unit_rules}, T0)

    assert len(result.trades) > 20
    total_pnl = sum(t.pnl for t in result.trades)
    assert result.equity_curve.iloc[-1] == pytest.approx(cfg.backtest.starting_balance + total_pnl)
    # every trade was charged fees on both sides
    assert all(t.fees > 0 for t in result.trades)
    # never invested more than the $10 budget
    assert all(t.entry_price * t.amount <= cfg.risk.budget_usdt for t in result.trades)


async def test_no_trading_before_start(unit_rules):
    cfg = AppConfig(market=MarketConfig(timeframe="1h"))  # test candles are hourly
    candles = {"XYZ/USDT": random_walk(600)}
    start = candles["XYZ/USDT"][300].time
    end = candles["XYZ/USDT"][450].time
    result = await run_backtest(cfg, EveryNCandles(), candles, {"XYZ/USDT": unit_rules}, start, end)
    assert result.trades
    assert all(start < t.entry_time and t.exit_time <= end + HOUR for t in result.trades)


async def test_cache_survives_network_failure(tmp_path):
    from datetime import timedelta

    from tradebot.infrastructure.market_data.cached import CachedMarketData

    candles = random_walk(50)

    class FlakyMarketData:
        calls = 0

        async def fetch_candles(self, symbol, timeframe, start, end):
            self.calls += 1
            if self.calls > 1:
                raise TimeoutError("binance timeout")
            return [c for c in candles if start <= c.time < end]

    cache = CachedMarketData(FlakyMarketData(), tmp_path)
    first = await cache.fetch_candles("XYZ/USDT", "1h", T0, T0 + timedelta(hours=50))
    # later request needs newer candles, but the network fails -> cached data is returned
    later = await cache.fetch_candles("XYZ/USDT", "1h", T0, T0 + timedelta(hours=60))
    assert len(first) == len(later) == 50
