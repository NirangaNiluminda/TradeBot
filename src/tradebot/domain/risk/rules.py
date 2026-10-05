"""Risk rules (Chain of Responsibility pattern).

A BUY idea (`TradeProposal`) passes through the rules one by one. Each rule
can change the proposal (e.g. make it smaller) or reject it with a reason.
SELL orders never go through these rules: exiting must never be blocked.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass

from tradebot.domain.models import RiskAssessment, RiskLevel, SymbolRules, TradeProposal


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    """What the risk rules need to know about our money right now."""

    equity: float  # cash + value of coins held
    day_start_equity: float  # equity at 00:00 UTC today
    free_quote: float  # USDT available to spend
    invested_quote: float  # USDT currently tied up in open positions (at cost)
    open_positions: int


@dataclass(frozen=True, slots=True)
class RiskContext:
    portfolio: PortfolioSnapshot
    risk: RiskAssessment
    rules: SymbolRules


class Rejected(Exception):
    """Raised by a rule to stop the chain and refuse the trade."""


class RiskRule(ABC):
    @abstractmethod
    def apply(self, proposal: TradeProposal, ctx: RiskContext) -> None:
        """Modify `proposal` in place, or raise `Rejected`."""


class DailyLossLimit(RiskRule):
    """Stop opening trades for the rest of the day after losing too much."""

    def __init__(self, limit_pct: float) -> None:
        self.limit_pct = limit_pct

    def apply(self, proposal: TradeProposal, ctx: RiskContext) -> None:
        p = ctx.portfolio
        if p.day_start_equity <= 0:
            return
        loss_pct = (p.day_start_equity - p.equity) / p.day_start_equity * 100
        if loss_pct >= self.limit_pct:
            raise Rejected(f"daily loss {loss_pct:.1f}% hit the {self.limit_pct}% limit")


class MaxOpenPositions(RiskRule):
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum

    def apply(self, proposal: TradeProposal, ctx: RiskContext) -> None:
        if ctx.portfolio.open_positions >= self.maximum:
            raise Rejected(
                f"already {ctx.portfolio.open_positions} open position(s), max {self.maximum}"
            )


class PositionSize(RiskRule):
    """Decide how many USDT to spend: a % of what's left of the trading budget."""

    def __init__(self, budget_quote: float, size_pct: float, slots: int = 1) -> None:
        # slots > 1: the budget is split in equal parts, one per position (multi-coin bots)
        self.budget_quote, self.size_pct, self.slots = budget_quote, size_pct, max(1, slots)

    def apply(self, proposal: TradeProposal, ctx: RiskContext) -> None:
        p = ctx.portfolio
        available = min(p.free_quote, self.budget_quote - p.invested_quote)
        if self.slots > 1:
            available = min(available, self.budget_quote / self.slots)
        if available <= 0:
            raise Rejected("trading budget fully used")
        proposal.quote_amount = available * self.size_pct / 100
        proposal.notes.append(
            f"size {proposal.quote_amount:.2f} ({self.size_pct}% of {available:.2f})"
        )


class PercentExitLevels(RiskRule):
    """Stop-loss and take-profit a fixed % away from the entry price."""

    def __init__(self, stop_loss_pct: float, take_profit_pct: float) -> None:
        self.stop_loss_pct, self.take_profit_pct = stop_loss_pct, take_profit_pct

    def apply(self, proposal: TradeProposal, ctx: RiskContext) -> None:
        proposal.stop_loss = proposal.price * (1 - self.stop_loss_pct / 100)
        proposal.take_profit = proposal.price * (1 + self.take_profit_pct / 100)


class StopOnlyExit(RiskRule):
    """A wide safety stop-loss and NO take-profit: the strategy decides when to sell.

    For trend following, where capping winners would cut off the big trends.
    """

    def __init__(self, stop_loss_pct: float) -> None:
        self.stop_loss_pct = stop_loss_pct

    def apply(self, proposal: TradeProposal, ctx: RiskContext) -> None:
        proposal.stop_loss = proposal.price * (1 - self.stop_loss_pct / 100)
        proposal.take_profit = math.inf
        proposal.notes.append(f"safety stop -{self.stop_loss_pct}%, no take-profit")


class AtrExitLevels(RiskRule):
    """Stop-loss and take-profit based on volatility (ATR = typical move per candle).

    Calm market -> tight stop; wild market -> wider stop. This avoids being
    stopped out by normal noise, and works the same on any timeframe.
    The stop distance is kept between `min_stop_pct` and `max_stop_pct`.
    """

    def __init__(
        self,
        stop_atr_mult: float,
        take_profit_atr_mult: float,
        min_stop_pct: float,
        max_stop_pct: float,
        fallback: PercentExitLevels,
    ) -> None:
        self.stop_mult, self.tp_mult = stop_atr_mult, take_profit_atr_mult
        self.min_stop_pct, self.max_stop_pct = min_stop_pct, max_stop_pct
        self.fallback = fallback

    def apply(self, proposal: TradeProposal, ctx: RiskContext) -> None:
        atr, price = proposal.atr, proposal.price
        if not math.isfinite(atr) or atr <= 0:
            self.fallback.apply(proposal, ctx)
            proposal.notes.append("ATR unavailable, used % exits")
            return
        stop_pct = atr * self.stop_mult / price * 100
        stop_pct = min(max(stop_pct, self.min_stop_pct), self.max_stop_pct)
        tp_pct = stop_pct * self.tp_mult / self.stop_mult  # keep the reward:risk ratio
        proposal.stop_loss = price * (1 - stop_pct / 100)
        proposal.take_profit = price * (1 + tp_pct / 100)
        proposal.notes.append(f"stop -{stop_pct:.1f}% / target +{tp_pct:.1f}% (ATR based)")


class NewsRisk(RiskRule):
    """React to the market risk level (from news / LLM).

    DANGER  -> reject.  CAUTION -> smaller size and a tighter stop-loss.
    """

    def __init__(self, caution_size_mult: float, caution_stop_mult: float) -> None:
        self.caution_size_mult, self.caution_stop_mult = caution_size_mult, caution_stop_mult

    def apply(self, proposal: TradeProposal, ctx: RiskContext) -> None:
        level = ctx.risk.level
        if level is RiskLevel.DANGER:
            raise Rejected(f"market risk DANGER: {ctx.risk.reason}")
        if level is RiskLevel.CAUTION:
            proposal.quote_amount *= self.caution_size_mult
            stop_distance = (proposal.price - proposal.stop_loss) * self.caution_stop_mult
            proposal.stop_loss = proposal.price - stop_distance
            proposal.notes.append(f"CAUTION: size x{self.caution_size_mult}, tighter stop")


class MinOrderSize(RiskRule):
    """Binance refuses orders below its minimum (about $5) — check before sending."""

    def apply(self, proposal: TradeProposal, ctx: RiskContext) -> None:
        r = ctx.rules
        amount = r.round_amount(proposal.quote_amount / proposal.price)
        if amount < r.min_amount or amount * proposal.price < r.min_notional:
            raise Rejected(
                f"order {proposal.quote_amount:.2f} below exchange minimum {r.min_notional:.2f}"
            )
