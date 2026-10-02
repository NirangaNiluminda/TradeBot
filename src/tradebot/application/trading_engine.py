"""The trading engine — the heart of the bot.

On every closed candle it:
  1. records stop-loss / take-profit exits that happened during the candle
  2. asks the strategy for a signal
  3. SELL -> closes the position;  BUY -> asks the risk manager, then buys and
     places automatic stop-loss / take-profit exits

The engine only talks to ports (ExchangePort, MarketRiskPort, IndicatorFeed),
so the exact same code runs in backtests, on the testnet and live.

Money: the engine keeps its OWN cash ledger (`self.cash`), starting from the
trading budget. The exchange account may hold much more (the testnet gives
10,000 fake USDT) — the bot only ever counts and risks its own budget.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from tradebot.application.approvals import ApprovalQueue
from tradebot.application.bot_control import BotControl
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
        starting_cash: float,
        quote_asset: str = "USDT",
        control: BotControl | None = None,
        approvals: ApprovalQueue | None = None,
    ) -> None:
        self.strategy = strategy
        self.feed = feed
        self.exchange = exchange
        self.risk_manager = risk_manager
        self.market_risk = market_risk
        self.bus = bus
        self.timeframe_delta = timeframe_delta
        self.quote_asset = quote_asset
        self.control = control or BotControl()
        self.approvals = approvals

        self.cash = starting_cash  # the bot's own USDT, not the whole exchange balance
        self.positions: dict[str, Position] = {}
        self.last_price: dict[str, float] = {}
        self._rules: dict[str, SymbolRules] = {}
        self._day: datetime | None = None
        self._day_start_equity = 0.0

    # ------------------------------------------------------------------ main loop
    async def on_candle(self, symbol: str, candle: Candle) -> None:
        now = candle.time + self.timeframe_delta  # the candle has just closed
        self.last_price[symbol] = candle.close

        await self.sync_exits()
        self._roll_trading_day(now)

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
            if not self.control.state.allows_new_trades:
                self.bus.publish(TradeRejected(now, symbol, f"bot is {self.control.state.name}"))
                return
            await self._try_open(symbol, candle.close, view.atr, now, signal.reason)

    async def sync_exits(self) -> None:
        """Record stop-loss / take-profit fills (called every candle, and more often when live)."""
        for fill in await self.exchange.fetch_exit_fills():
            position = self.positions.get(fill.symbol)
            if position is not None:
                self._finish(position, fill, fill.reason or ExitReason.STOP_LOSS, fill.time)

    async def restore(self, positions: list[Position]) -> None:
        """After a restart: take back open positions saved in the database."""
        for p in positions:
            self.positions[p.symbol] = p
            self.last_price.setdefault(p.symbol, p.entry_price)
            await self.exchange.restore_exit_orders(p)

    # ------------------------------------------------------------------ portfolio
    def equity(self) -> float:
        """The bot's cash + current value of the coins it holds."""
        held = sum(
            p.amount * self.last_price.get(s, p.entry_price) for s, p in self.positions.items()
        )
        return self.cash + held

    async def snapshot(self) -> PortfolioSnapshot:
        exchange_free = await self.exchange.get_free_balance(self.quote_asset)
        return PortfolioSnapshot(
            equity=self.equity(),
            day_start_equity=self._day_start_equity,
            free_quote=min(self.cash, exchange_free),
            invested_quote=sum(p.cost for p in self.positions.values()),
            open_positions=len(self.positions),
        )

    def _roll_trading_day(self, now: datetime) -> None:
        """Remember equity at the start of each UTC day (for the daily loss limit)."""
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if day != self._day:
            self._day = day
            self._day_start_equity = self.equity()

    async def _symbol_rules(self, symbol: str) -> SymbolRules:
        if symbol not in self._rules:
            self._rules[symbol] = await self.exchange.get_symbol_rules(symbol)
        return self._rules[symbol]

    # ------------------------------------------------------------------ opening
    async def _try_open(
        self, symbol: str, price: float, atr: float, now: datetime, reason: str = ""
    ) -> None:
        proposal = await self._checked_proposal(symbol, price, atr, now)
        if proposal is None:
            return
        if self.control.manual_approval and self.approvals is not None:
            self.approvals.request(proposal, reason, now)  # wait for your decision
            return
        await self.execute_buy(proposal, now)

    async def _checked_proposal(
        self, symbol: str, price: float, atr: float, now: datetime
    ) -> TradeProposal | None:
        """Run a buy idea through the risk rules. None (and an event) if rejected."""
        ctx = RiskContext(
            portfolio=await self.snapshot(),
            risk=self.market_risk.current(),
            rules=await self._symbol_rules(symbol),
        )
        decision = self.risk_manager.evaluate(TradeProposal(symbol, price, atr=atr), ctx)
        if not decision.approved:
            self.bus.publish(TradeRejected(now, symbol, decision.reason, decision.proposal))
            return None
        return decision.proposal

    async def approve(self, request_id: str, now: datetime) -> str:
        """You approved a pending buy: re-check everything at today's price, then buy."""
        if self.approvals is None:
            return "approval mode is not enabled"
        req = self.approvals.take(request_id, now)
        if req is None:
            return "request not found or expired"
        symbol = req.proposal.symbol
        if symbol in self.positions or not self.control.state.allows_new_trades:
            self.approvals.resolved(req, "failed", now)
            return f"cannot buy now (bot {self.control.state.name}, or already holding {symbol})"
        price = self.last_price.get(symbol, req.proposal.price)
        proposal = await self._checked_proposal(symbol, price, req.proposal.atr, now)
        if proposal is None:
            self.approvals.resolved(req, "failed", now)
            return "risk rules no longer allow this trade"
        await self.execute_buy(proposal, now)
        ok = symbol in self.positions
        self.approvals.resolved(req, "approved" if ok else "failed", now)
        return "bought" if ok else "exchange rejected the order"

    async def execute_buy(self, proposal: TradeProposal, now: datetime) -> None:
        """Send an approved proposal to the exchange (manual mode calls this after approval)."""
        symbol = proposal.symbol
        try:
            fill = await self.exchange.market_buy(symbol, proposal.quote_amount)
        except OrderRejected as exc:
            self.bus.publish(TradeRejected(now, symbol, f"exchange rejected: {exc}", proposal))
            return
        self.cash -= fill.amount * fill.price + fill.fee

        # Keep the same % distances, measured from the price we actually got.
        stop = fill.price * proposal.stop_loss / proposal.price
        target = fill.price * proposal.take_profit / proposal.price
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
        try:
            position.exit_order_id = await self.exchange.place_exit_orders(
                symbol, fill.amount, stop, target
            )
        except OrderRejected as exc:
            # Never keep a position without a stop-loss: sell it straight away.
            self.bus.publish(TradeRejected(now, symbol, f"could not place stop-loss: {exc}"))
            await self._sell(position, ExitReason.EMERGENCY, now)
            return
        self.bus.publish(PositionOpened(now, position, fill))

    # ------------------------------------------------------------------ closing
    async def close_position(self, symbol: str, reason: ExitReason, now: datetime) -> None:
        position = self.positions[symbol]
        await self.exchange.cancel_exit_orders(symbol)
        await self.sync_exits()  # the stop/target may have filled just before we cancelled
        if symbol not in self.positions:
            return
        try:
            await self._sell(position, reason, now)
        except OrderRejected as exc:
            # Couldn't sell: put the protection back so the position isn't left naked.
            self.bus.publish(TradeRejected(now, symbol, f"sell failed, exits restored: {exc}"))
            position.exit_order_id = await self.exchange.place_exit_orders(
                symbol, position.amount, position.stop_loss, position.take_profit
            )

    async def close_all(self, reason: ExitReason, now: datetime) -> None:
        for symbol in list(self.positions):
            await self.close_position(symbol, reason, now)

    async def _sell(self, position: Position, reason: ExitReason, now: datetime) -> None:
        fill = await self.exchange.market_sell(position.symbol, position.amount)
        self._finish(position, fill, reason, now)

    def _finish(self, position: Position, fill: Fill, reason: ExitReason, now: datetime) -> None:
        del self.positions[position.symbol]
        self.cash += fill.amount * fill.price - fill.fee
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
