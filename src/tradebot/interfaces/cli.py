"""Command-line interface.

uv run tradebot strategies              list available strategies
uv run tradebot download                download candles into data/
uv run tradebot backtest                backtest the strategy from config.yaml
uv run tradebot backtest -s breakout    backtest another strategy
uv run tradebot compare                 backtest ALL strategies and rank them
uv run tradebot optimize                honest walk-forward test of ALL strategies
uv run tradebot optimize -s ensemble    ...of one strategy, with step-by-step details
uv run tradebot news                    latest headlines
uv run tradebot risk                    LLM market-risk check from the news
"""

from __future__ import annotations

import asyncio
import csv
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
import yaml
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from tradebot import bootstrap
from tradebot.application.backtest import BacktestResult
from tradebot.application.event_bus import EventBus
from tradebot.application.metrics import PerformanceReport, evaluate
from tradebot.application.optimizer import (
    WalkForwardOptimizer,
    WalkForwardResult,
    build_candidates,
)
from tradebot.config import AppConfig, Secrets, load_config
from tradebot.domain.strategies import strategy_factory

app = typer.Typer(
    help="Binance trading bot — backtesting and (soon) live trading.", no_args_is_help=True
)
console = Console()
logging.basicConfig(
    level=logging.WARNING, format="%(message)s", handlers=[RichHandler(console=console)]
)

ConfigOpt = Annotated[Path | None, typer.Option("--config", "-c", help="Path to config.yaml")]
TimeframeOpt = Annotated[str | None, typer.Option("--timeframe", "-t", help="e.g. 15m, 1h, 4h")]
SymbolsOpt = Annotated[
    str | None, typer.Option("--symbols", help="Comma list, e.g. BTC/USDT,ETH/USDT")
]
DaysOpt = Annotated[int | None, typer.Option("--days", "-d", help="Test the last N days")]


def _load(
    config: Path | None, timeframe: str | None, symbols: str | None, days: int | None
) -> AppConfig:
    cfg = load_config(config) if config else load_config()
    if timeframe:
        cfg.market.timeframe = timeframe
    if symbols:
        cfg.market.symbols = [s.strip().upper() for s in symbols.split(",")]
    if days:
        cfg.backtest.days, cfg.backtest.start = days, None
    return cfg


# ---------------------------------------------------------------------------- commands
@app.command()
def strategies() -> None:
    """List all available strategies."""
    table = Table(title="Available strategies")
    table.add_column("name", style="bold cyan")
    table.add_column("what it does")
    for name, cls in strategy_factory.available().items():
        table.add_row(name, cls.description)
    console.print(table)


@app.command()
def download(
    config: ConfigOpt = None,
    timeframe: TimeframeOpt = None,
    symbols: SymbolsOpt = None,
    days: DaysOpt = None,
) -> None:
    """Download historical candles from Binance into the local cache."""
    cfg = _load(config, timeframe, symbols, days)

    async def main() -> None:
        market_data, client = bootstrap.build_market_data(cfg)
        try:
            start, end = cfg.backtest.period()
            # extra history so slow indicators (e.g. EMA 200) are ready on day one
            candles = await bootstrap.load_candles(
                market_data,
                cfg.market.symbols,
                cfg.market.timeframe,
                start,
                end,
                warmup_candles=1000,
            )
        finally:
            await client.close()
        for symbol, series in candles.items():
            console.print(
                f"[green]✓[/] {symbol} {cfg.market.timeframe}: {len(series)} candles "
                f"({series[0].time:%Y-%m-%d} → {series[-1].time:%Y-%m-%d})"
            )

    asyncio.run(main())


@app.command()
def backtest(
    strategy: Annotated[str | None, typer.Option("--strategy", "-s", help="Strategy name")] = None,
    config: ConfigOpt = None,
    timeframe: TimeframeOpt = None,
    symbols: SymbolsOpt = None,
    days: DaysOpt = None,
    save: Annotated[bool, typer.Option(help="Save the trade list to data/backtests/")] = False,
) -> None:
    """Backtest one strategy and show a detailed report."""
    cfg = _load(config, timeframe, symbols, days)
    name = strategy or cfg.strategy.name
    params = cfg.strategy.params if name == cfg.strategy.name else {}
    result = asyncio.run(_run(cfg, [(name, params)]))[0]
    report = evaluate(result)
    _print_report(result, report)
    if save:
        path = _save_trades(cfg, result)
        console.print(f"\nTrades saved to [cyan]{path}[/]")


@app.command()
def compare(
    config: ConfigOpt = None,
    timeframe: TimeframeOpt = None,
    symbols: SymbolsOpt = None,
    days: DaysOpt = None,
) -> None:
    """Backtest every strategy (default settings) on the same data and rank them."""
    cfg = _load(config, timeframe, symbols, days)
    specs = [(name, {}) for name in strategy_factory.available()]
    results = asyncio.run(_run(cfg, specs))
    rows = sorted(
        ((r, evaluate(r)) for r in results), key=lambda x: x[1].total_return_pct, reverse=True
    )

    first = results[0]
    table = Table(
        title=f"Strategy comparison — {', '.join(first.symbols)} {first.timeframe}, "
        f"{first.start:%Y-%m-%d} → {first.end:%Y-%m-%d}, start ${first.starting_balance:.2f}"
    )
    for col in [
        "strategy",
        "return",
        "final $",
        "trades",
        "win rate",
        "profit factor",
        "max drawdown",
        "fees $",
    ]:
        table.add_column(col, justify="left" if col == "strategy" else "right")
    for r, p in rows:
        table.add_row(
            r.strategy,
            _pct(p.total_return_pct),
            f"{p.final_equity:.2f}",
            str(p.trades),
            f"{p.win_rate_pct:.0f}%",
            _pf(p.profit_factor),
            f"-{p.max_drawdown_pct:.1f}%",
            f"{p.fees_paid:.2f}",
        )
    console.print(table)
    console.print(f"Buy & hold benchmark (average of symbols): {_pct(rows[0][1].buy_and_hold_pct)}")
    console.print(
        "[dim]Past results do not guarantee future results. Check trade counts: "
        "fewer than ~30 trades is not enough evidence.[/]"
    )


@app.command()
def optimize(
    strategy: Annotated[
        str | None, typer.Option("--strategy", "-s", help="One strategy (default: all)")
    ] = None,
    years: Annotated[float, typer.Option(help="How many years of history to use")] = 3,
    train_days: Annotated[int, typer.Option(help="Length of each training period")] = 365,
    test_days: Annotated[int, typer.Option(help="Length of each unseen test period")] = 90,
    workers: Annotated[int, typer.Option(help="CPU cores to use")] = max(
        1, min(3, (os.cpu_count() or 2) - 1)
    ),
    config: ConfigOpt = None,
    timeframe: TimeframeOpt = None,
    symbols: SymbolsOpt = None,
) -> None:
    """Honest test: tune settings on older data, score them only on newer unseen data."""
    cfg = _load(config, timeframe, symbols, None)
    names = [strategy] if strategy else list(strategy_factory.available())
    end = datetime.now(UTC)
    start = end - timedelta(days=365 * years)

    async def load() -> tuple[dict, dict]:
        warmup = 700  # enough for EMA 200 (x3) on any setting in the grids
        market_data, client = bootstrap.build_market_data(cfg)
        try:
            candles = await bootstrap.load_candles(
                market_data, cfg.market.symbols, cfg.market.timeframe, start, end, warmup
            )
            return candles, await bootstrap.load_symbol_rules(market_data, cfg.market.symbols)
        finally:
            await client.close()

    with console.status("Loading candles (first run downloads from Binance)..."):
        candles, rules = asyncio.run(load())

    results: list[WalkForwardResult] = []
    with (
        bootstrap.ParallelBacktestBatch(cfg, candles, rules, workers) as batch,
        console.status("Optimizing...") as status,
    ):
        optimizer = WalkForwardOptimizer(
            batch, train_days, test_days, on_progress=lambda m: status.update(f"Optimizing {m}")
        )
        for name in names:
            candidates = build_candidates(name, strategy_factory)
            results.append(optimizer.run(candidates, start, end))

    if len(results) == 1:
        _print_folds(results[0])
    _print_walk_forward_summary(results, cfg, optimizer.min_trades)


def _print_folds(r: WalkForwardResult) -> None:
    table = Table(title=f"{r.strategy} — each step: best settings on training data → unseen test")
    for col in ["test period", "chosen settings", "train", "TEST", "trades", "buy & hold"]:
        table.add_column(col)
    for f in r.folds:
        table.add_row(
            f"{f.test_start:%Y-%m-%d} → {f.test_end:%Y-%m-%d}",
            f.best.label(),
            _pct(f.train.total_return_pct),
            _pct(f.test.total_return_pct),
            str(f.test.trades),
            _pct(f.test_buy_and_hold_pct),
        )
    console.print(table)


def _verdict(r: WalkForwardResult) -> str:
    trades, ret, pf = len(r.test_trades), r.test_return_pct, r.test_profit_factor
    if trades < 20:
        return "[yellow]too few trades to judge[/]"
    if ret > 0 and pf >= 1.2 and r.profitable_folds >= len(r.folds) / 2:
        return "[green]promising[/]"
    if ret > 0:
        return "[yellow]weak / inconsistent[/]"
    return "[red]not working[/]"


def _print_walk_forward_summary(
    results: list[WalkForwardResult], cfg: AppConfig, min_trades: int
) -> None:
    first = results[0]
    if not first.folds:
        console.print("[red]Not enough history for even one train+test step. Use more --years.[/]")
        return
    period = f"{first.folds[0].test_start:%Y-%m-%d} → {first.folds[-1].test_end:%Y-%m-%d}"
    table = Table(
        title=f"Walk-forward results on UNSEEN data ({period}, {len(first.folds)} steps, "
        f"{cfg.market.timeframe}, {', '.join(cfg.market.symbols)})"
    )
    cols = [
        "strategy",
        "test return",
        "trades",
        "profit factor",
        "steps won",
        "worst drop",
        "verdict",
    ]
    for col in cols:
        table.add_column(col)
    ranked = sorted(results, key=lambda r: r.test_return_pct, reverse=True)
    for r in ranked:
        table.add_row(
            r.strategy,
            _pct(r.test_return_pct),
            str(len(r.test_trades)),
            _pf(r.test_profit_factor),
            f"{r.profitable_folds}/{len(r.folds)}",
            f"-{r.worst_test_drawdown_pct:.1f}%",
            _verdict(r),
        )
    console.print(table)
    console.print(
        f"Benchmarks over the same test periods:  buy & hold {_pct(first.buy_and_hold_pct)}"
        f"   |   cash (do nothing) {_pct(0.0)}"
    )

    best = ranked[0]
    rec, rec_train = best.recommended, best.recommended_train
    if rec is None or rec_train is None:
        return
    console.print(
        f"\n[bold]Settings for {best.strategy} chosen on the most recent "
        f"{(first.folds[0].test_start - first.folds[0].train_start).days} days:[/]"
    )
    if rec_train.trades < min_trades:
        console.print("[yellow]Warning: too few trades in that window — treat with caution.[/]")
    snippet = {
        "strategy": {"name": rec.strategy, "params": rec.params_dict},
        "risk": {k: v for k, v in rec.risk_dict.items()},
    }
    console.print(yaml.safe_dump(snippet, sort_keys=False).rstrip())
    console.print(
        "[dim]Copy into config/config.yaml only if the verdict is 'promising'. "
        "Test periods are short — results vary a lot between market phases.[/]"
    )


@app.command()
def news(config: ConfigOpt = None) -> None:
    """Show the latest headlines the LLM will read."""
    cfg = _load(config, None, None, None)

    async def main() -> None:
        async with bootstrap.build_http(cfg) as http:
            service = bootstrap.build_news_risk(cfg, Secrets(), http, EventBus())
            items = await service.collect_news()
        table = Table(title=f"{len(items)} headlines from the last {cfg.news.lookback_hours:g}h")
        for col in ["age", "source", "headline"]:
            table.add_column(col)
        now = datetime.now(UTC)
        for item in items:
            age = (now - item.published).total_seconds() / 3600
            table.add_row(f"{age:.1f}h", item.source, item.title)
        console.print(table)

    asyncio.run(main())


@app.command()
def risk(
    provider: Annotated[
        str | None, typer.Option("--provider", "-p", help="Use only this LLM, e.g. groq")
    ] = None,
    config: ConfigOpt = None,
) -> None:
    """Ask the LLM for the current market risk level based on the news."""
    cfg = _load(config, None, None, None)
    if provider:
        cfg.llm.providers = [provider]

    async def main() -> None:
        async with bootstrap.build_http(cfg) as http:
            service = bootstrap.build_news_risk(cfg, Secrets(), http, EventBus())
            with console.status("Reading news and asking the LLM..."):
                assessment = await service.refresh()
        colour = {"NORMAL": "green", "CAUTION": "yellow", "DANGER": "red"}[assessment.level.value]
        console.rule("[bold]Market risk from news")
        console.print(f"Risk level:  [bold {colour}]{assessment.level.value}[/]")
        console.print(f"Reason:      {assessment.reason}")
        console.print(
            f"Sentiment:   {assessment.sentiment:+.2f}  (-1 very negative … +1 very positive)"
        )
        if assessment.affected_coins:
            console.print(f"Coins:       {', '.join(assessment.affected_coins)}")
        console.print(f"Answered by: {assessment.source}")
        console.print(f"Headlines:   {len(service.last_headlines)} read")
        effect = {
            "NORMAL": "trade normally",
            "CAUTION": "half-size trades with tighter stops (at $10 this means: skip new buys)",
            "DANGER": "no new buys",
        }[assessment.level.value]
        console.print(f"[dim]Effect on the bot: {effect}[/]")

    asyncio.run(main())


# ---------------------------------------------------------------------------- helpers
async def _run(cfg: AppConfig, specs: list[tuple[str, dict]]) -> list[BacktestResult]:
    strategies_ = [bootstrap.build_strategy(n, p) for n, p in specs]
    warmup = max(s.warmup for s in strategies_) * 3
    start, end = cfg.backtest.period()
    market_data, client = bootstrap.build_market_data(cfg)
    try:
        with console.status("Loading candles (first run downloads from Binance)..."):
            candles = await bootstrap.load_candles(
                market_data, cfg.market.symbols, cfg.market.timeframe, start, end, warmup
            )
            rules = await bootstrap.load_symbol_rules(market_data, cfg.market.symbols)
        results = []
        for strat in strategies_:
            with console.status(f"Backtesting {strat.name}..."):
                results.append(await bootstrap.run_backtest(cfg, strat, candles, rules, start))
        return results
    finally:
        await client.close()


def _print_report(r: BacktestResult, p: PerformanceReport) -> None:
    console.rule(f"[bold]Backtest: {r.strategy}")
    console.print(f"Parameters: {r.params}")
    console.print(
        f"Symbols: {', '.join(r.symbols)}  |  Timeframe: {r.timeframe}  |  "
        f"Period: {r.start:%Y-%m-%d} → {r.end:%Y-%m-%d}"
    )

    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_row("Starting balance", f"${r.starting_balance:.2f}")
    t.add_row("Final balance", f"${p.final_equity:.2f}")
    t.add_row("Total return", _pct(p.total_return_pct))
    t.add_row("Buy & hold (benchmark)", _pct(p.buy_and_hold_pct))
    t.add_row("Trades", str(p.trades))
    t.add_row("Win rate", f"{p.win_rate_pct:.1f}%")
    t.add_row("Average win / loss", f"{_pct(p.avg_win_pct)} / {_pct(p.avg_loss_pct)}")
    t.add_row("Profit factor", _pf(p.profit_factor) + "  [dim](>1 = profitable)[/]")
    t.add_row("Max drawdown", f"-{p.max_drawdown_pct:.1f}%  [dim](worst drop from a peak)[/]")
    t.add_row("Fees paid", f"${p.fees_paid:.2f}")
    t.add_row("Exits", ", ".join(f"{k}: {v}" for k, v in p.exit_reasons.items()) or "-")
    console.print(t)

    if r.rejections:
        console.print("\n[bold]Trades blocked by risk rules / exchange:[/]")
        for reason, count in r.rejections.most_common():
            console.print(f"  {count:>4} × {reason}")

    if r.trades:
        last = Table(title="Last 10 trades")
        for col in ["symbol", "entry", "exit", "entry $", "exit $", "P/L $", "P/L %", "reason"]:
            last.add_column(col)
        for tr in r.trades[-10:]:
            colour = "green" if tr.pnl > 0 else "red"
            last.add_row(
                tr.symbol,
                f"{tr.entry_time:%m-%d %H:%M}",
                f"{tr.exit_time:%m-%d %H:%M}",
                f"{tr.entry_price:,.2f}",
                f"{tr.exit_price:,.2f}",
                f"[{colour}]{tr.pnl:+.3f}[/]",
                f"[{colour}]{tr.pnl_pct:+.2f}%[/]",
                tr.exit_reason.value,
            )
        console.print(last)


def _save_trades(cfg: AppConfig, r: BacktestResult) -> Path:
    out_dir = cfg.data.resolved() / "backtests"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{r.strategy}_{r.timeframe}_{datetime.now(UTC):%Y%m%d_%H%M%S}.csv"
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "symbol",
                "entry_time",
                "entry_price",
                "exit_time",
                "exit_price",
                "amount",
                "fees",
                "pnl",
                "pnl_pct",
                "exit_reason",
            ]
        )
        for t in r.trades:
            w.writerow(
                [
                    t.symbol,
                    t.entry_time.isoformat(),
                    t.entry_price,
                    t.exit_time.isoformat(),
                    t.exit_price,
                    t.amount,
                    round(t.fees, 6),
                    round(t.pnl, 6),
                    round(t.pnl_pct, 3),
                    t.exit_reason.value,
                ]
            )
    return path


def _pct(v: float) -> str:
    colour = "green" if v > 0 else "red" if v < 0 else "white"
    return f"[{colour}]{v:+.2f}%[/]"


def _pf(v: float) -> str:
    return "∞" if v == float("inf") else f"{v:.2f}"


if __name__ == "__main__":
    app()
