"""Real Binance spot trading via ccxt (Adapter implementing ExchangePort).

`sandbox=True` uses the Binance Spot Testnet (fake money), otherwise real money.

Binance details handled here so the rest of the bot doesn't need to know them:
- buys use `quoteOrderQty` ("spend exactly X USDT")
- the buy fee is usually taken in the COIN, so we hold slightly less than we bought;
  the Fill reports the amount we actually hold, and the fee converted to USDT
- stop-loss + take-profit are one OCO order (orderList/oco endpoint)
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from typing import Any

import ccxt.async_support as ccxt

from tradebot.domain.models import ExitReason, Fill, Position, Side, SymbolRules
from tradebot.domain.ports import OrderRejected
from tradebot.infrastructure.ccxt_common import symbol_rules

log = logging.getLogger(__name__)

# Rough fee rate when Binance charges the fee in BNB (we can't price BNB cheaply here).
_BNB_FEE_RATE = 0.00075
_SINGLE = "single:"  # exit id prefix for a lone stop-loss order (no take-profit)


class CcxtExchange:
    def __init__(
        self, exchange_id: str, api_key: str, api_secret: str, sandbox: bool, quote_asset: str
    ) -> None:
        self._ex = getattr(ccxt, exchange_id)(
            {"apiKey": api_key, "secret": api_secret, "enableRateLimit": True}
        )
        if sandbox:
            self._ex.set_sandbox_mode(True)
        self.sandbox = sandbox
        self.quote_asset = quote_asset
        self._markets_loaded = False
        self._exits: dict[str, tuple[str, float]] = {}  # symbol -> (OCO list id, amount)
        self._pending_fills: list[Fill] = []

    # ------------------------------------------------------------------ info
    async def _load(self) -> None:
        if not self._markets_loaded:
            await self._ex.load_markets()
            self._markets_loaded = True

    async def get_free_balance(self, asset: str) -> float:
        balance = await self._ex.fetch_balance()
        return float(balance["free"].get(asset) or 0.0)

    async def get_symbol_rules(self, symbol: str) -> SymbolRules:
        await self._load()
        return symbol_rules(self._ex, symbol)

    # ------------------------------------------------------------------ orders
    async def market_buy(self, symbol: str, quote_amount: float) -> Fill:
        await self._load()
        cost = self._ex.cost_to_precision(symbol, quote_amount)
        order = await self._call(
            self._ex.create_order, symbol, "market", "buy", None, None, {"quoteOrderQty": cost}
        )
        return await self._to_fill(symbol, Side.BUY, order)

    async def market_sell(self, symbol: str, amount: float) -> Fill:
        await self._load()
        qty = float(self._ex.amount_to_precision(symbol, amount))
        order = await self._call(self._ex.create_order, symbol, "market", "sell", qty)
        return await self._to_fill(symbol, Side.SELL, order)

    async def place_exit_orders(
        self, symbol: str, amount: float, stop_loss: float, take_profit: float
    ) -> str:
        await self._load()
        if not math.isfinite(take_profit):
            # No take-profit (trend strategies): a single stop-loss order instead of an OCO.
            order = await self._call(
                self._ex.create_order, symbol, "STOP_LOSS", "sell",
                float(self._ex.amount_to_precision(symbol, amount)), None,
                {"stopPrice": self._ex.price_to_precision(symbol, stop_loss)},
            )  # fmt: skip
            order_id = f"{_SINGLE}{order['id']}"
            self._exits[symbol] = (order_id, amount)
            return order_id
        params = {
            "symbol": self._ex.market(symbol)["id"],
            "side": "SELL",
            "quantity": self._ex.amount_to_precision(symbol, amount),
            "aboveType": "LIMIT_MAKER",  # take-profit: sell at our target or better
            "abovePrice": self._ex.price_to_precision(symbol, take_profit),
            "belowType": "STOP_LOSS",  # stop-loss: market sell once price drops to the stop
            "belowStopPrice": self._ex.price_to_precision(symbol, stop_loss),
        }
        result = await self._call(self._ex.privatePostOrderListOco, params)
        list_id = str(result["orderListId"])
        self._exits[symbol] = (list_id, amount)
        return list_id

    async def restore_exit_orders(self, position: Position) -> None:
        if position.exit_order_id:
            self._exits[position.symbol] = (position.exit_order_id, position.amount)

    async def cancel_exit_orders(self, symbol: str) -> None:
        entry = self._exits.pop(symbol, None)
        if entry is None:
            return
        await self._load()
        try:
            if entry[0].startswith(_SINGLE):
                await self._ex.cancel_order(entry[0].removeprefix(_SINGLE), symbol)
            else:
                await self._ex.privateDeleteOrderList(
                    {"symbol": self._ex.market(symbol)["id"], "orderListId": entry[0]}
                )
        except ccxt.ExchangeError:
            # Probably filled a moment ago — record that fill instead of selling twice.
            fill = await self._filled_exit(symbol, entry[0])
            if fill:
                self._pending_fills.append(fill)

    async def fetch_exit_fills(self) -> list[Fill]:
        fills, self._pending_fills = self._pending_fills, []
        for symbol, (list_id, _) in list(self._exits.items()):
            if list_id.startswith(_SINGLE):
                order = await self._ex.fetch_order(list_id.removeprefix(_SINGLE), symbol)
                if order["status"] in ("closed", "canceled", "expired", "rejected"):
                    del self._exits[symbol]
                    if order["status"] == "closed":
                        fills.append(
                            await self._to_fill(symbol, Side.SELL, order, ExitReason.STOP_LOSS)
                        )
                continue
            status = await self._ex.privateGetOrderList({"orderListId": list_id})
            if status["listOrderStatus"] != "ALL_DONE":
                continue
            del self._exits[symbol]
            fill = await self._filled_exit(symbol, list_id, status)
            if fill:
                fills.append(fill)
            else:
                log.warning("Exit orders for %s ended without a fill (cancelled outside the bot?)",
                            symbol)  # fmt: skip
        return fills

    async def withdrawals_enabled(self) -> bool:
        """True if this API key may WITHDRAW funds (dangerous). Live accounts only."""
        restrictions = await self._ex.sapiGetAccountApiRestrictions()
        return bool(restrictions.get("enableWithdrawals"))

    async def close(self) -> None:
        await self._ex.close()

    # ------------------------------------------------------------------ helpers
    async def _filled_exit(
        self, symbol: str, list_id: str, status: dict[str, Any] | None = None
    ) -> Fill | None:
        if list_id.startswith(_SINGLE):
            order = await self._ex.fetch_order(list_id.removeprefix(_SINGLE), symbol)
            if order["status"] == "closed":
                return await self._to_fill(symbol, Side.SELL, order, ExitReason.STOP_LOSS)
            return None
        status = status or await self._ex.privateGetOrderList({"orderListId": list_id})
        for leg in status.get("orders", []):
            order = await self._ex.fetch_order(str(leg["orderId"]), symbol)
            if order["status"] == "closed" and float(order.get("filled") or 0) > 0:
                kind = str(order["info"].get("type", ""))
                reason = ExitReason.STOP_LOSS if "STOP" in kind else ExitReason.TAKE_PROFIT
                return await self._to_fill(symbol, Side.SELL, order, reason)
        return None

    async def _call(self, fn: Any, *args: Any) -> Any:
        try:
            return await fn(*args)
        except (ccxt.InsufficientFunds, ccxt.InvalidOrder, ccxt.BadRequest) as exc:
            raise OrderRejected(str(exc)) from exc
        except ccxt.NetworkError as exc:
            # The order may or may not have reached Binance; the startup check will tell.
            log.error("Network error while sending an order: %s", exc)
            raise OrderRejected(f"network error: {type(exc).__name__}") from exc

    async def _to_fill(
        self, symbol: str, side: Side, order: dict[str, Any], reason: ExitReason | None = None
    ) -> Fill:
        base, quote = symbol.split("/")
        gross = float(order["filled"])
        price = float(order.get("average") or order.get("price") or 0.0)
        fee_quote, fee_base = 0.0, 0.0
        fees = order.get("fees") or ([order["fee"]] if order.get("fee") else [])
        for fee in fees:
            currency, cost = fee.get("currency"), float(fee.get("cost") or 0.0)
            if currency == base:
                fee_base += cost
                fee_quote += cost * price
            elif currency == quote:
                fee_quote += cost
            elif cost:
                fee_quote += gross * price * _BNB_FEE_RATE
        amount = gross
        if side is Side.BUY:
            rules = await self.get_symbol_rules(symbol)
            amount = rules.round_amount(gross - fee_base)  # what we can actually sell later
        stamp = order.get("lastTradeTimestamp") or order.get("timestamp")
        time = datetime.fromtimestamp(stamp / 1000, tz=UTC) if stamp else datetime.now(UTC)
        return Fill(symbol, side, amount, price, fee_quote, time, reason)
