"""Turn a backtest result into easy-to-read performance numbers."""

from __future__ import annotations

from dataclasses import dataclass

from tradebot.application.backtest import BacktestResult
from tradebot.domain.models import Trade


@dataclass(frozen=True)
class PerformanceReport:
    final_equity: float
    total_return_pct: float
    trades: int
    win_rate_pct: float
    avg_win_pct: float
    avg_loss_pct: float
    profit_factor: float  # money won / money lost. Above 1 = profitable
    max_drawdown_pct: float  # worst drop from a peak
    fees_paid: float
    buy_and_hold_pct: float  # average over symbols — the "do nothing" benchmark
    exit_reasons: dict[str, int]


@dataclass(frozen=True)
class TradeStats:
    """Accuracy of a list of finished trades (live or backtest)."""

    count: int
    wins: int
    win_rate_pct: float
    avg_win_pct: float
    avg_loss_pct: float
    profit_factor: float
    total_pnl: float
    fees: float


def trade_stats(trades: list[Trade]) -> TradeStats:
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    won, lost = sum(t.pnl for t in wins), -sum(t.pnl for t in losses)
    return TradeStats(
        count=len(trades),
        wins=len(wins),
        win_rate_pct=len(wins) / len(trades) * 100 if trades else 0.0,
        avg_win_pct=sum(t.pnl_pct for t in wins) / len(wins) if wins else 0.0,
        avg_loss_pct=sum(t.pnl_pct for t in losses) / len(losses) if losses else 0.0,
        profit_factor=won / lost if lost else (float("inf") if won else 0.0),
        total_pnl=won - lost,
        fees=sum(t.fees for t in trades),
    )


def evaluate(result: BacktestResult) -> PerformanceReport:
    trades = result.trades
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)

    curve = result.equity_curve
    if curve.empty:
        final, max_dd = result.starting_balance, 0.0
    else:
        final = float(curve.iloc[-1])
        drawdown = (curve / curve.cummax() - 1) * 100
        max_dd = float(-drawdown.min())

    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t.exit_reason.value] = reasons.get(t.exit_reason.value, 0) + 1

    bh = result.buy_and_hold_pct
    return PerformanceReport(
        final_equity=final,
        total_return_pct=(final / result.starting_balance - 1) * 100,
        trades=len(trades),
        win_rate_pct=len(wins) / len(trades) * 100 if trades else 0.0,
        avg_win_pct=sum(t.pnl_pct for t in wins) / len(wins) if wins else 0.0,
        avg_loss_pct=sum(t.pnl_pct for t in losses) / len(losses) if losses else 0.0,
        profit_factor=gross_win / gross_loss if gross_loss else float("inf") if gross_win else 0.0,
        max_drawdown_pct=max_dd,
        fees_paid=sum(t.fees for t in trades),
        buy_and_hold_pct=sum(bh.values()) / len(bh) if bh else 0.0,
        exit_reasons=reasons,
    )
