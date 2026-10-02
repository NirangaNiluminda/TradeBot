"""A pretend exchange for backtests (Adapter implementing ExchangePort).

Realism built in:
- fees on every order (Binance default 0.1%)
- slippage on market orders (you usually get a slightly worse price)
- Binance minimum order size and quantity step
- stop-loss / take-profit checked against each candle's low / high. If BOTH were
  touched in the same candle we assume the stop-loss hit first (pessimistic).

Simplification: fees are charged in USDT. (Real Binance takes the buy fee in the
coin unless you pay fees with BNB — the live adapter will handle that.)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from tradebot.domain.models import Candle, ExitReason, Fill, Position, Side, SymbolRules
from tradebot.domain.ports import OrderRejected

_EPS = 1e-9


@dataclass(slots=True)
class _ExitOrder:
    amount: float
    stop_loss: float
    take_profit: float


class SimulatedExchange:
    def __init__(
        self,
        rules: dict[str, SymbolRules],
        starting_quote: float,
        timeframe_delta: timedelta,
        quote_asset: str = "USDT",
        fee_pct: float = 0.1,
        slippage_pct: float = 0.05,
    ) -> None:
        self.rules = rules
        self.quote_asset = quote_asset
        self.fee_rate = fee_pct / 100
        self.slippage = slippage_pct / 100
        self.timeframe_delta = timeframe_delta
        self.balances: dict[str, float] = {quote_asset: starting_quote}
        self._price: dict[str, float] = {}
        self._now: datetime | None = None
        self._exits: dict[str, _ExitOrder] = {}
        self._exit_fills: list[Fill] = []

    # ---------------------------------------------------------- driven by the backtest
    def on_candle(self, symbol: str, candle: Candle) -> None:
        """Simulate everything that happened during this candle."""
        close_time = candle.time + self.timeframe_delta
        order = self._exits.get(symbol)
        if order is not None:
            if candle.low <= order.stop_loss:
                # If the candle opened below our stop (a gap), we sell at the open — worse.
                price = min(candle.open, order.stop_loss) * (1 - self.slippage)
                self._fill_exit(symbol, order, price, ExitReason.STOP_LOSS, close_time)
            elif candle.high >= order.take_profit:
                # Limit order: fills at our target, or better if the candle gapped above it.
                price = max(candle.open, order.take_profit)
                self._fill_exit(symbol, order, price, ExitReason.TAKE_PROFIT, close_time)
        self._price[symbol] = candle.close
        self._now = close_time

    def _fill_exit(
        self, symbol: str, order: _ExitOrder, price: float, reason: ExitReason, time: datetime
    ) -> None:
        del self._exits[symbol]
        fee = order.amount * price * self.fee_rate
        self._add(self._base(symbol), -order.amount)
        self._add(self.quote_asset, order.amount * price - fee)
        self._exit_fills.append(Fill(symbol, Side.SELL, order.amount, price, fee, time, reason))

    # ---------------------------------------------------------- ExchangePort
    async def get_free_balance(self, asset: str) -> float:
        locked = sum(o.amount for s, o in self._exits.items() if self._base(s) == asset)
        return self.balances.get(asset, 0.0) - locked

    async def get_symbol_rules(self, symbol: str) -> SymbolRules:
        return self.rules[symbol]

    async def market_buy(self, symbol: str, quote_amount: float) -> Fill:
        rules = self.rules[symbol]
        price = self._current_price(symbol) * (1 + self.slippage)
        cash = self.balances.get(self.quote_asset, 0.0)
        spend = min(quote_amount, cash / (1 + self.fee_rate))  # leave room for the fee
        amount = rules.round_amount(spend / price)
        self._check_minimums(rules, amount, price)

        cost = amount * price
        fee = cost * self.fee_rate
        self._add(self.quote_asset, -(cost + fee))
        self._add(self._base(symbol), amount)
        return Fill(symbol, Side.BUY, amount, price, fee, self._time())

    async def market_sell(self, symbol: str, amount: float) -> Fill:
        free = await self.get_free_balance(self._base(symbol))
        if amount > free + _EPS:
            raise OrderRejected(f"insufficient {self._base(symbol)}: have {free}, want {amount}")
        price = self._current_price(symbol) * (1 - self.slippage)
        fee = amount * price * self.fee_rate
        self._add(self._base(symbol), -amount)
        self._add(self.quote_asset, amount * price - fee)
        return Fill(symbol, Side.SELL, amount, price, fee, self._time())

    async def place_exit_orders(
        self, symbol: str, amount: float, stop_loss: float, take_profit: float
    ) -> str:
        self._exits[symbol] = _ExitOrder(amount, stop_loss, take_profit)
        return f"sim-{symbol}"

    async def restore_exit_orders(self, position: Position) -> None:
        self._exits[position.symbol] = _ExitOrder(
            position.amount, position.stop_loss, position.take_profit
        )

    async def cancel_exit_orders(self, symbol: str) -> None:
        self._exits.pop(symbol, None)

    async def fetch_exit_fills(self) -> list[Fill]:
        fills, self._exit_fills = self._exit_fills, []
        return fills

    # ---------------------------------------------------------- helpers
    def _check_minimums(self, rules: SymbolRules, amount: float, price: float) -> None:
        if amount < rules.min_amount or amount * price < rules.min_notional - _EPS:
            raise OrderRejected(
                f"order value {amount * price:.2f} below minimum {rules.min_notional:.2f}"
            )

    def _current_price(self, symbol: str) -> float:
        try:
            return self._price[symbol]
        except KeyError:
            raise OrderRejected(f"no price yet for {symbol}") from None

    def _time(self) -> datetime:
        if self._now is None:
            raise OrderRejected("simulation has not started")
        return self._now

    def _add(self, asset: str, delta: float) -> None:
        self.balances[asset] = self.balances.get(asset, 0.0) + delta

    @staticmethod
    def _base(symbol: str) -> str:
        return symbol.split("/")[0]
