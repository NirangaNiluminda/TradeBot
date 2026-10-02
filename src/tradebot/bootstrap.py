"""Composition root — the ONLY place that knows which concrete adapters are used.

Everything else depends on ports (interfaces). To swap an implementation
(e.g. another exchange, a different LLM later), change it here or in config.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime

from tradebot.application.backtest import BacktestResult, BacktestRunner
from tradebot.application.event_bus import EventBus
from tradebot.application.indicator_feed import PrecomputedFeed
from tradebot.application.market_risk import StaticMarketRisk
from tradebot.application.optimizer import BacktestJob
from tradebot.application.trading_engine import TradingEngine
from tradebot.config import AppConfig, RiskConfig
from tradebot.domain.models import Candle, SymbolRules
from tradebot.domain.ports import MarketDataPort
from tradebot.domain.risk import (
    AtrExitLevels,
    DailyLossLimit,
    MaxOpenPositions,
    MinOrderSize,
    NewsRisk,
    PercentExitLevels,
    PositionSize,
    RiskManager,
    RiskRule,
)
from tradebot.domain.strategies import Strategy, strategy_factory
from tradebot.domain.timeframes import timeframe_delta
from tradebot.infrastructure.exchange.simulated import SimulatedExchange
from tradebot.infrastructure.market_data.cached import CachedMarketData
from tradebot.infrastructure.market_data.ccxt_market_data import CcxtMarketData


def build_market_data(cfg: AppConfig) -> tuple[MarketDataPort, CcxtMarketData]:
    """Returns (cached market data, raw client). Close the raw client when done."""
    client = CcxtMarketData(cfg.exchange.name)
    cache_dir = cfg.data.resolved() / "candles" / cfg.exchange.name
    return CachedMarketData(client, cache_dir), client


def build_exit_levels(risk: RiskConfig) -> RiskRule:
    percent = PercentExitLevels(risk.stop_loss_pct, risk.take_profit_pct)
    if risk.exit_mode == "percent":
        return percent
    return AtrExitLevels(
        risk.stop_atr_mult, risk.take_profit_atr_mult, risk.min_stop_pct, risk.max_stop_pct, percent
    )


def build_risk_manager(risk: RiskConfig) -> RiskManager:
    # Order matters: cheap "should we trade at all?" checks first, sizing after.
    return RiskManager(
        [
            DailyLossLimit(risk.daily_loss_limit_pct),
            MaxOpenPositions(risk.max_open_positions),
            PositionSize(risk.budget_usdt, risk.position_size_pct),
            build_exit_levels(risk),
            NewsRisk(risk.caution_size_mult, risk.caution_stop_mult),
            MinOrderSize(),
        ]
    )


def build_strategy(name: str, params: dict | None = None) -> Strategy:
    return strategy_factory.create(name, params)


async def load_candles(
    market_data: MarketDataPort,
    symbols: list[str],
    timeframe: str,
    start: datetime,
    end: datetime,
    warmup_candles: int = 0,
) -> dict[str, list[Candle]]:
    """Download (or read cached) candles, including extra warm-up history before `start`."""
    fetch_from = start - timeframe_delta(timeframe) * warmup_candles
    return {s: await market_data.fetch_candles(s, timeframe, fetch_from, end) for s in symbols}


async def load_symbol_rules(
    market_data: MarketDataPort, symbols: list[str]
) -> dict[str, SymbolRules]:
    return {s: await market_data.fetch_symbol_rules(s) for s in symbols}


async def run_backtest(
    cfg: AppConfig,
    strategy: Strategy,
    candles: dict[str, list[Candle]],
    rules: dict[str, SymbolRules],
    start: datetime,
    end: datetime | None = None,
) -> BacktestResult:
    tf = cfg.market.timeframe
    bus = EventBus()
    simulator = SimulatedExchange(
        rules=rules,
        starting_quote=cfg.backtest.starting_balance,
        timeframe_delta=timeframe_delta(tf),
        quote_asset=cfg.exchange.quote_asset,
        fee_pct=cfg.backtest.fee_pct,
        slippage_pct=cfg.backtest.slippage_pct,
    )
    engine = TradingEngine(
        strategy=strategy,
        feed=PrecomputedFeed(strategy, candles),
        exchange=simulator,
        risk_manager=build_risk_manager(cfg.risk),
        market_risk=StaticMarketRisk(),  # no historical news -> always NORMAL
        bus=bus,
        timeframe_delta=timeframe_delta(tf),
        quote_asset=cfg.exchange.quote_asset,
    )
    runner = BacktestRunner(engine, simulator, bus)
    return await runner.run(candles, start, cfg.backtest.starting_balance, tf, end)


# ---------------------------------------------------------------- batch backtests
# Worker processes receive the (large) candle data once, at start-up, instead of
# with every job. These module-level names hold it inside each worker.
_worker_state: dict = {}


def _init_worker(cfg: AppConfig, candles: dict[str, list[Candle]], rules: dict) -> None:
    _worker_state.update(cfg=cfg, candles=candles, rules=rules)


def _run_job(job: BacktestJob) -> BacktestResult:
    cfg: AppConfig = _worker_state["cfg"]
    c = job.candidate
    job_cfg = cfg.model_copy(update={"risk": cfg.risk.model_copy(update=c.risk_dict)})
    strategy = build_strategy(c.strategy, c.params_dict)
    return asyncio.run(
        run_backtest(
            job_cfg, strategy, _worker_state["candles"], _worker_state["rules"], job.start, job.end
        )
    )


class ParallelBacktestBatch:
    """Runs many backtests on several CPU cores (implements BacktestBatch).

    Use as a context manager so the worker processes are shut down afterwards.
    workers=1 runs everything in this process (simpler to debug).
    """

    def __init__(
        self, cfg: AppConfig, candles: dict[str, list[Candle]], rules: dict, workers: int
    ) -> None:
        self.workers = workers
        self._pool: ProcessPoolExecutor | None = None
        if workers > 1:
            self._pool = ProcessPoolExecutor(
                workers, initializer=_init_worker, initargs=(cfg, candles, rules)
            )
        else:
            _init_worker(cfg, candles, rules)

    def run_many(self, jobs: list[BacktestJob]) -> list[BacktestResult]:
        if self._pool is None:
            return [_run_job(j) for j in jobs]
        return list(
            self._pool.map(_run_job, jobs, chunksize=max(1, len(jobs) // (self.workers * 4)))
        )

    def __enter__(self) -> ParallelBacktestBatch:
        return self

    def __exit__(self, *exc: object) -> None:
        if self._pool is not None:
            self._pool.shutdown(cancel_futures=True)
