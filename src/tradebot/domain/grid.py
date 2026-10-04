"""Grid trading logic (pure domain code, no exchange).

A grid places a ladder of price levels around the current price:

    level 4  ── 104 ──  sell target for slot 3
    level 3  ── 102 ──  sell target for slot 2
    level 2  ── 100 ──  (current price)
    level 1  ──  98 ──  BUY waiting here (slot 1) -> when filled, SELL waits at 100
    level 0  ──  96 ──  BUY waiting here (slot 0) -> when filled, SELL waits at 98

Every "slot" i is either waiting to BUY at level i, or holding coins and waiting
to SELL at level i+1. One buy + one sell = one "cycle" with a small profit.
When the price wiggles up and down, cycles repeat many times a day.

The risk: if the price falls below the whole ladder, the grid holds coins bought
on the way down. An optional stop-loss sells everything if that goes too far.

This class is also the fill simulator: it is fed 1-minute candles (real or
historical) and decides which limit orders would have filled.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from tradebot.domain.models import Candle, SymbolRules


class SlotState(StrEnum):
    IDLE = "idle"  # above the price when the grid was built; activates on the way up
    BUY = "buy"  # buy order waiting at this level
    SELL = "sell"  # holding coins, sell order waiting one level higher


@dataclass(frozen=True)
class GridConfig:
    budget: float = 100.0  # USDT the grid may use
    range_pct: float = 4.0  # ladder goes from -range% to +range% around the price
    levels: int = 10  # number of price levels (levels-1 slots)
    stop_loss_pct: float | None = 5.0  # sell all if price closes this % below the ladder
    recenter_minutes: int = 60  # rebuild after price stays outside the ladder this long
    cooldown_minutes: int = 240  # wait after a stop-loss before rebuilding
    fee_pct: float = 0.1  # per order
    slippage_pct: float = 0.05  # only for the stop-loss market sell
    fill_through_pct: float = 0.01  # price must trade this far past a level to count as filled

    def __post_init__(self) -> None:
        if self.levels < 3 or self.range_pct <= 0 or self.budget <= 0:
            raise ValueError("grid needs >= 3 levels, a positive range and budget")

    @property
    def slot_budget(self) -> float:
        return self.budget / (self.levels - 1)

    def label(self) -> str:
        sl = f"{self.stop_loss_pct}%" if self.stop_loss_pct else "none"
        return f"range ±{self.range_pct}% · {self.levels} levels · stop-loss {sl}"


@dataclass
class Slot:
    index: int
    buy_price: float
    sell_price: float
    state: SlotState
    amount: float = 0.0
    cost: float = 0.0  # USDT paid incl. fee


@dataclass(frozen=True)
class GridFill:
    time: datetime
    side: str  # buy / sell / stop
    price: float
    amount: float
    fee: float
    profit: float = 0.0  # set on sells: proceeds - cost of that slot


@dataclass
class GridBook:
    config: GridConfig
    rules: SymbolRules
    cash: float = 0.0
    slots: list[Slot] = field(default_factory=list)
    lower: float = 0.0
    upper: float = 0.0
    active: bool = False
    _outside_since: datetime | None = None
    _cooldown_until: datetime | None = None
    rebuilds: int = 0
    stopouts: int = 0

    def __post_init__(self) -> None:
        if not self.cash:
            self.cash = self.config.budget

    # ------------------------------------------------------------------ building
    def build(self, center: float, now: datetime) -> None:
        """Lay out a fresh ladder around `center` (only when holding nothing)."""
        if self.holding:
            raise RuntimeError("cannot rebuild a grid that still holds coins")
        c = self.config
        lo, hi = center * (1 - c.range_pct / 100), center * (1 + c.range_pct / 100)
        prices = [lo * (hi / lo) ** (i / (c.levels - 1)) for i in range(c.levels)]
        self.slots = [
            Slot(i, prices[i], prices[i + 1],
                 SlotState.BUY if prices[i] < center else SlotState.IDLE)
            for i in range(c.levels - 1)
        ]  # fmt: skip
        self.lower, self.upper, self.active = lo, hi, True
        self._outside_since = None
        self.rebuilds += 1

    @property
    def holding(self) -> bool:
        return any(s.state is SlotState.SELL for s in self.slots)

    def held_amount(self) -> float:
        return sum(s.amount for s in self.slots if s.state is SlotState.SELL)

    def equity(self, price: float) -> float:
        return self.cash + self.held_amount() * price

    # ------------------------------------------------------------------ per candle
    def on_candle(self, candle: Candle, allow_buys: bool = True) -> list[GridFill]:
        """Process one 1-minute candle. `allow_buys=False` (e.g. news DANGER) pauses buys."""
        now = candle.time + timedelta(minutes=1)
        if not self.active:
            if self._cooldown_until is None or now >= self._cooldown_until:
                self.build(candle.close, now)
            return []

        fills: list[GridFill] = []
        through = self.config.fill_through_pct / 100
        start = [s.state for s in self.slots]  # an order placed this minute can't also fill
        for slot, state in zip(self.slots, start, strict=True):
            if (
                state is SlotState.BUY
                and allow_buys
                and candle.low <= slot.buy_price * (1 - through)
            ):
                fills.extend(self._buy(slot, min(candle.open, slot.buy_price), now))
            elif state is SlotState.SELL and candle.high >= slot.sell_price * (1 + through):
                fills.append(self._sell(slot, max(candle.open, slot.sell_price), now))
            elif state is SlotState.IDLE and candle.close > slot.sell_price:
                slot.state = SlotState.BUY  # price is now above it: a later dip can be bought

        fills.extend(self._check_stop_loss(candle, now))
        self._check_recenter(candle, now)
        return fills

    def _buy(self, slot: Slot, price: float, now: datetime) -> list[GridFill]:
        spend = min(self.config.slot_budget, self.cash / (1 + self.config.fee_pct / 100))
        amount = self.rules.round_amount(spend / price)
        if amount < self.rules.min_amount or amount * price < self.rules.min_notional:
            return []  # not enough cash for a valid order: keep waiting
        fee = amount * price * self.config.fee_pct / 100
        self.cash -= amount * price + fee
        slot.state, slot.amount, slot.cost = SlotState.SELL, amount, amount * price + fee
        return [GridFill(now, "buy", price, amount, fee)]

    def _sell(self, slot: Slot, price: float, now: datetime, side: str = "sell") -> GridFill:
        fee = slot.amount * price * self.config.fee_pct / 100
        proceeds = slot.amount * price - fee
        self.cash += proceeds
        fill = GridFill(now, side, price, slot.amount, fee, proceeds - slot.cost)
        slot.state, slot.amount, slot.cost = SlotState.BUY, 0.0, 0.0
        return fill

    def _check_stop_loss(self, candle: Candle, now: datetime) -> list[GridFill]:
        sl = self.config.stop_loss_pct
        if sl is None or candle.close >= self.lower * (1 - sl / 100) or not self.holding:
            return []
        price = candle.close * (1 - self.config.slippage_pct / 100)
        fills = [
            self._sell(s, price, now, side="stop") for s in self.slots if s.state is SlotState.SELL
        ]
        self.active, self.stopouts = False, self.stopouts + 1
        self._cooldown_until = now + timedelta(minutes=self.config.cooldown_minutes)
        return fills

    def _check_recenter(self, candle: Candle, now: datetime) -> None:
        if not self.active or self.holding:
            self._outside_since = None
            return
        outside = candle.close > self.upper or candle.close < self.lower
        if not outside:
            self._outside_since = None
        elif self._outside_since is None:
            self._outside_since = now
        elif now - self._outside_since >= timedelta(minutes=self.config.recenter_minutes):
            self.build(candle.close, now)


def drawdown_pct(values: list[float]) -> float:
    peak, worst = -math.inf, 0.0
    for v in values:
        peak = max(peak, v)
        worst = max(worst, (peak - v) / peak * 100 if peak > 0 else 0.0)
    return worst
