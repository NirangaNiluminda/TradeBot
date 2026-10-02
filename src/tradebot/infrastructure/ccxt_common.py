from __future__ import annotations

from typing import Any

import ccxt.async_support as ccxt

from tradebot.domain.models import SymbolRules


def symbol_rules(exchange: Any, symbol: str) -> SymbolRules:
    """Read min order size / step from a ccxt exchange whose markets are loaded."""
    market = exchange.market(symbol)
    step = market["precision"]["amount"]
    if exchange.precisionMode == ccxt.DECIMAL_PLACES:
        step = 10 ** -int(step)
    limits = market["limits"]
    return SymbolRules(
        symbol=symbol,
        min_notional=float(limits["cost"]["min"] or 0.0),
        amount_step=float(step),
        min_amount=float(limits["amount"]["min"] or 0.0),
    )
