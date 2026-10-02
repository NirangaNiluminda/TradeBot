"""Strategy 2 — RSI mean reversion: buy short dips inside an uptrend."""

from __future__ import annotations

from typing import Any

import pandas as pd

from tradebot.domain.models import Action, Signal
from tradebot.domain.strategies.base import Strategy, StrategyContext, register
from tradebot.domain.strategies.frame import Frame
from tradebot.domain.strategies.indicators import ema, rsi


@register("rsi_reversion")
class RsiReversion(Strategy):
    description = (
        "BUY when RSI climbs back above the oversold level (a dip is recovering) while "
        "price is above the trend EMA. SELL when RSI reaches the exit level."
    )
    param_grid = {"oversold": [30, 35, 40], "exit_level": [55, 65], "trend": [0, 200]}

    # oversold=35 (not the textbook 30): in an uptrend RSI rarely falls below 30 on
    # 4h candles, so 30 produced only ~1 trade per year — too few to be useful.
    def __init__(
        self, period: int = 14, oversold: float = 35, exit_level: float = 60, trend: int = 200
    ) -> None:
        self.period, self.oversold, self.exit_level, self.trend = (
            period,
            oversold,
            exit_level,
            trend,
        )

    @property
    def warmup(self) -> int:
        return max(self.period, self.trend) + 1

    def params(self) -> dict[str, Any]:
        return {
            "period": self.period,
            "oversold": self.oversold,
            "exit_level": self.exit_level,
            "trend": self.trend,
        }

    def indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["rsi"] = rsi(out["close"], self.period)
        out["ema_trend"] = ema(out["close"], self.trend) if self.trend else 0.0
        return out

    def describe(self, history: Frame) -> str:
        r = history["rsi"][-1]
        trend = "above" if history["close"][-1] > history["ema_trend"][-1] else "below"
        return (
            f"RSI is {r:.0f}; price is {trend} the trend line. Buys when RSI climbs back above "
            f"{self.oversold:.0f} in an uptrend; sells when RSI reaches {self.exit_level:.0f}."
        )

    def signal(self, history: Frame, ctx: StrategyContext) -> Signal:
        if len(history) < 2:
            return Signal.hold("not enough data")
        r_prev, r_now = history["rsi"][-2], history["rsi"][-1]
        close, trend = history["close"][-1], history["ema_trend"][-1]

        if r_now >= self.exit_level:
            return Signal(Action.SELL, f"RSI {r_now:.0f} reached exit level {self.exit_level:.0f}")
        if r_prev < self.oversold <= r_now:
            if close > trend:
                return Signal(Action.BUY, f"RSI recovered above {self.oversold:.0f} in uptrend")
            return Signal.hold("RSI recovery but price below trend EMA")
        return Signal.hold()
