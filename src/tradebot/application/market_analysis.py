"""A quick, plain-language picture of each market (no LLM needed).

Shown on the dashboard, sent to Telegram on Start, and given to the LLM when
explanations are switched on.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any

from tradebot.application.frames import candles_to_frame
from tradebot.domain.models import Action, Candle
from tradebot.domain.strategies.base import Strategy, StrategyContext
from tradebot.domain.strategies.frame import Frame
from tradebot.domain.strategies.indicators import adx, atr, ema, rsi


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    price: float
    change_24h_pct: float
    change_7d_pct: float
    trend: str  # uptrend / downtrend / sideways
    trend_strength: float  # ADX: above ~25 = strong trend
    rsi: float
    volatility_pct: float  # typical move per candle, % of price
    strategy_view: str  # what the strategy is waiting for
    status: str  # holding / buy signal now / waiting

    def to_dict(self) -> dict[str, Any]:
        return {k: (None if isinstance(v, float) and math.isnan(v) else v)
                for k, v in asdict(self).items()}  # fmt: skip


def analyze(
    symbol: str,
    candles: list[Candle],
    step: timedelta,
    strategy: Strategy,
    in_position: bool,
    live_price: float | None = None,
) -> MarketSnapshot | None:
    if len(candles) < 50:
        return None
    df = candles_to_frame(candles)
    close = df["close"]
    price = live_price or float(close.iloc[-1])
    ema50, ema200 = float(ema(close, 50).iloc[-1]), float(ema(close, 200).iloc[-1])
    if price > ema50 > ema200:
        trend = "uptrend"
    elif price < ema50 < ema200:
        trend = "downtrend"
    else:
        trend = "sideways"

    def change(hours: float) -> float:
        n = max(1, round(timedelta(hours=hours) / step))
        return (price / float(close.iloc[-1 - n]) - 1) * 100 if len(close) > n else math.nan

    history = Frame.from_dataframe(strategy.indicators(df)).tail(50)
    signal = strategy.signal(history, StrategyContext(in_position=in_position))
    if in_position:
        status = "holding"
    elif signal.action is Action.BUY:
        status = "buy signal now"
    else:
        status = "waiting"
    return MarketSnapshot(
        symbol=symbol,
        price=price,
        change_24h_pct=change(24),
        change_7d_pct=change(24 * 7),
        trend=trend,
        trend_strength=float(adx(df, 14).iloc[-1]),
        rsi=float(rsi(close, 14).iloc[-1]),
        volatility_pct=float(atr(df, 14).iloc[-1]) / price * 100,
        strategy_view=strategy.describe(history),
        status=status,
    )
