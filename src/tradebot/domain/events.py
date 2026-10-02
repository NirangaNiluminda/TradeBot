"""Things that happen inside the bot.

The trading engine publishes these on the event bus (Observer pattern).
Anyone can listen — the backtest report, the database logger, the dashboard,
Telegram — without the engine knowing they exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from tradebot.domain.models import Fill, Position, RiskAssessment, Signal, Trade, TradeProposal


@dataclass(frozen=True, slots=True)
class Event:
    time: datetime


@dataclass(frozen=True, slots=True)
class SignalGenerated(Event):
    symbol: str
    signal: Signal


@dataclass(frozen=True, slots=True)
class TradeRejected(Event):
    symbol: str
    reason: str
    proposal: TradeProposal | None = None


@dataclass(frozen=True, slots=True)
class PositionOpened(Event):
    position: Position
    fill: Fill


@dataclass(frozen=True, slots=True)
class PositionClosed(Event):
    trade: Trade


@dataclass(frozen=True, slots=True)
class RiskChanged(Event):
    """The market risk level changed (e.g. NORMAL -> DANGER after bad news)."""

    previous: RiskAssessment
    current: RiskAssessment
