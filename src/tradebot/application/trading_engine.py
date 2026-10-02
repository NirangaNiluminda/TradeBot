"""The trading engine — the heart of the bot.

On every closed candle it:
  1. records stop-loss / take-profit exits that happened during the candle
  2. asks the strategy for a signal
  3. SELL -> closes the position;  BUY -> asks the risk manager, then buys and
     places automatic stop-loss / take-profit exits

The engine only talks to ports (ExchangePort, MarketRiskPort, IndicatorFeed),
so the exact same code runs in backtests, on the testnet and live.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from tradebot.application.event_bus import EventBus
from tradebot.application.indicator_feed import IndicatorFeed
from tradebot.domain.events import PositionClosed, PositionOpened, SignalGenerated, TradeRejected
from tradebot.domain.models import (
    Action,
    Candle,
    ExitReason,
    Fill,
    Position,
    SymbolRules,
    Trade,
    TradeProposal,
)
from tradebot.domain.ports import ExchangePort, MarketRiskPort, OrderRejected
from tradebot.domain.risk import PortfolioSnapshot, RiskContext, RiskManager
from tradebot.domain.strategies.base import Strategy, StrategyContext


class TradingEngine:
    def __init__(
        self,
        strategy: Strategy,
        feed: IndicatorFeed,
        exchange: ExchangePort,
        risk_manager: RiskManager,
        market_risk: MarketRiskPort,
        bus: EventBus,
        timeframe_delta: timedelta,
        quote_asset: str = "USDT",
    ) -> None:
        self.strategy = strategy
        self.feed = feed
        self.exchange = exchange
        self.risk_manager = risk_manager
        self.market_risk = market_risk
        self.bus = bus
        self.timeframe_delta = timeframe_delta
        self.quote_asset = quote_asset

        self.positions: dict[str, Position] = {}
        self.last_price: dict[str, float] = {}
        self._rules: dict[str, SymbolRules] = {}
        self._day: datetime | None = None
        self._day_start_equity = 0.0

    # ------------------------------------------------------------------ main loop
    async def on_candle(self, symbol: str, candle: Candle) -> None:
        now = candle.time + self.timeframe_delta  # the candle has just closed
        self.last_price[symbol] = candle.close

        await self._record_exit_fills()
        await self._roll_trading_day(now)

        view = self.feed.update(symbol, candle)
        risk = self.market_risk.current()
        ctx = StrategyContext(in_position=symbol in self.positions, risk=risk)
        signal = self.strategy.signal(view.history, ctx)
        if signal.action is Action.HOLD:
            return
        self.bus.publish(SignalGenerated(now, symbol, signal))

        if signal.action is Action.SELL and symbol in self.positions:
            await self.close_position(symbol, ExitReason.STRATEGY, now)
        elif signal.action is Action.BUY and symbol not in self.positions:
            await self._try_open(symbol, candle.close, view.atr, now)

    # ------------------------------------------------------------------ portfolio
    async def equity(self) -> float:
        """Cash + current value of all coins we hold."""
        cash = await self.exchange.get_free_balance(self.quote_asset)
        held = sum(
            p.amount * self.last_price.get(s, p.entry_price) for s, p in self.positions.items()
        )
        return cash + held

    async def snapshot(self) -> PortfolioSnapshot:
        return PortfolioSnapshot(
            equity=await self.equity(),
            day_start_equity=self._day_start_equity,
            free_quote=await self.exchange.get_free_balance(self.quote_asset),
            invested_quote=sum(p.cost for p in self.positions.values()),
            open_positions=len(self.positions),
        )

    async def _roll_trading_day(self, now: datetime) -> None:
        """Remember equity at the start of each UTC day (for the daily loss limit)."""
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if day != self._day:
            self._day = day
            self._day_start_equity = await self.equity()

    async def _symbol_rules(self, symbol: str) -> SymbolRules:
        if symbol not in self._rules:
            self._rules[symbol] = await self.exchange.get_symbol_rules(symbol)
        return self._rules[symbol]

    # ------------------------------------------------------------------ opening
    async def _try_open(self, symbol: str, price: float, atr: float, now: datetime) -> None:
        ctx = RiskContext(
            portfolio=await self.snapshot(),
            risk=self.market_risk.current(),
            rules=await self._symbol_rules(symbol),
        )
        proposal = TradeProposal(symbol=symbol, price=price, atr=atr)
        decision = self.risk_manager.evaluate(proposal, ctx)
        if not decision.approved:
            self.bus.publish(TradeRejected(now, symbol, decision.reason, decision.proposal))
            return
        await self.execute_buy(decision.proposal, now)

    async def execute_buy(self, proposal: TradeProposal, now: datetime) -> None:
        """Send an approved proposal to the exchange (manual mode calls this after approval)."""
        symbol = proposal.symbol
        try:
            fill = await self.exchange.market_buy(symbol, proposal.quote_amount)
        except OrderRejected as exc:
            self.bus.publish(TradeRejected(now, symbol, f"exchange rejected: {exc}", proposal))
            return

        # Keep the same % distances, measured from the price we actually got.
        stop = fill.price * proposal.stop_loss / proposal.price
        target = fill.price * proposal.take_profit / proposal.price
        await self.exchange.place_exit_orders(symbol, fill.amount, stop, target)

        position = Position(
            symbol=symbol,
            amount=fill.amount,
            entry_price=fill.price,
            entry_time=fill.time,
            entry_fee=fill.fee,
            stop_loss=stop,
            take_profit=target,
            strategy=self.strategy.name,
        )
        self.positions[symbol] = position
        self.bus.publish(PositionOpened(now, position, fill))

    # ------------------------------------------------------------------ closing
    async def close_position(self, symbol: str, reason: ExitReason, now: datetime) -> None:
        position = self.positions[symbol]
        await self.exchange.cancel_exit_orders(symbol)
        fill = await self.exchange.market_sell(symbol, position.amount)
        self._finish(position, fill, reason, now)

    async def close_all(self, reason: ExitReason, now: datetime) -> None:
        for symbol in list(self.positions):
            await self.close_position(symbol, reason, now)

    async def _record_exit_fills(self) -> None:
        for fill in await self.exchange.fetch_exit_fills():
            position = self.positions.get(fill.symbol)
            if position is not None:
                self._finish(position, fill, fill.reason or ExitReason.STOP_LOSS, fill.time)

    def _finish(self, position: Position, fill: Fill, reason: ExitReason, now: datetime) -> None:
        del self.positions[position.symbol]
        trade = Trade(
            symbol=position.symbol,
            strategy=position.strategy,
            entry_time=position.entry_time,
            entry_price=position.entry_price,
            exit_time=fill.time,
            exit_price=fill.price,
            amount=position.amount,
            fees=position.entry_fee + fill.fee,
            exit_reason=reason,
        )
        self.bus.publish(PositionClosed(now, trade))
