"""Core business objects.

These are plain data classes with no dependency on Binance, any LLM, or any
database. Every other layer speaks in terms of these objects.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Action(StrEnum):
    """What a strategy suggests doing right now."""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class RiskLevel(StrEnum):
    """How dangerous the market looks right now (filled by the news/LLM service later).

    NORMAL  -> trade as usual
    CAUTION -> smaller trades, tighter stop-loss
    DANGER  -> no new buys
    """

    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    DANGER = "DANGER"


class ExitReason(StrEnum):
    STRATEGY = "strategy"  # the strategy said SELL
    STOP_LOSS = "stop_loss"  # price fell to our safety stop
    TAKE_PROFIT = "take_profit"  # price rose to our profit target
    END_OF_TEST = "end_of_test"  # backtest finished while still holding


@dataclass(frozen=True, slots=True)
class Candle:
    """One price bar: Open, High, Low, Close and Volume for a time period."""

    time: datetime  # candle OPEN time, UTC
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class Signal:
    """A strategy's suggestion plus a human-readable reason (shown on dashboard/Telegram)."""

    action: Action
    reason: str = ""

    @staticmethod
    def hold(reason: str = "") -> Signal:
        return Signal(Action.HOLD, reason)


@dataclass(frozen=True, slots=True)
class SymbolRules:
    """Exchange limits for a trading pair, e.g. Binance BTC/USDT.

    min_notional: smallest order value in quote currency (about $5 on Binance)
    amount_step:  quantity must be a multiple of this (0.00001 BTC)
    min_amount:   smallest quantity allowed
    """

    symbol: str
    min_notional: float
    amount_step: float
    min_amount: float

    def round_amount(self, amount: float) -> float:
        """Round a quantity DOWN to the allowed step (never buy more than we can pay for)."""
        if self.amount_step <= 0:
            return amount
        steps = math.floor(amount / self.amount_step + 1e-9)
        decimals = max(0, -int(math.floor(math.log10(self.amount_step))))
        return round(steps * self.amount_step, decimals)


@dataclass(frozen=True, slots=True)
class Fill:
    """A completed (filled) order reported by the exchange."""

    symbol: str
    side: Side
    amount: float  # quantity of the coin
    price: float  # average fill price
    fee: float  # fee paid, in quote currency (USDT)
    time: datetime
    reason: ExitReason | None = None  # set for automatic exits (stop-loss / take-profit)

    @property
    def cost(self) -> float:
        return self.amount * self.price


@dataclass(slots=True)
class Position:
    """A coin we currently hold, plus its safety exits."""

    symbol: str
    amount: float
    entry_price: float
    entry_time: datetime
    entry_fee: float
    stop_loss: float
    take_profit: float
    strategy: str = ""

    @property
    def cost(self) -> float:
        return self.amount * self.entry_price


@dataclass(frozen=True, slots=True)
class Trade:
    """A finished round trip: buy then sell."""

    symbol: str
    strategy: str
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    amount: float
    fees: float
    exit_reason: ExitReason

    @property
    def pnl(self) -> float:
        """Profit or loss in quote currency, after fees."""
        return (self.exit_price - self.entry_price) * self.amount - self.fees

    @property
    def pnl_pct(self) -> float:
        cost = self.entry_price * self.amount
        return self.pnl / cost * 100 if cost else 0.0


@dataclass(slots=True)
class TradeProposal:
    """A BUY idea travelling through the risk manager.

    Each risk rule may change it (e.g. make it smaller) or reject it.
    `notes` records what every rule did, so you can see WHY a trade was sized/rejected.
    """

    symbol: str
    price: float
    atr: float = math.nan  # typical price move per candle (volatility), for stop distances
    quote_amount: float = 0.0  # how many USDT to spend
    stop_loss: float = 0.0
    take_profit: float = 0.0
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    """Market risk opinion, e.g. from the news/LLM service."""

    level: RiskLevel = RiskLevel.NORMAL
    reason: str = "no news analysis configured"
    sentiment: float = 0.0  # -1 very negative ... +1 very positive
    affected_coins: tuple[str, ...] = ()
    source: str = "static"  # which LLM provider produced it
    time: datetime | None = None  # when it was produced


@dataclass(frozen=True, slots=True)
class NewsItem:
    """One headline from a news source."""

    title: str
    source: str
    published: datetime
    summary: str = ""
    url: str = ""
