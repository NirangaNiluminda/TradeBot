"""Strategy 1 — EMA crossover with a long-term trend filter."""

from __future__ import annotations

from typing import Any

import pandas as pd

from tradebot.domain.models import Action, Signal
from tradebot.domain.strategies.base import Strategy, StrategyContext, register
from tradebot.domain.strategies.frame import Frame
from tradebot.domain.strategies.indicators import crossed_above, crossed_below, ema


@register("ema_cross")
class EmaCross(Strategy):
    description = (
        "BUY when the fast EMA crosses above the slow EMA while price is above the "
        "long-term trend EMA. SELL when the fast EMA crosses back below."
    )
    param_grid = {"fast": [9, 12, 20], "slow": [26, 50], "trend": [0, 200]}

    def __init__(self, fast: int = 9, slow: int = 21, trend: int = 200) -> None:
        if fast >= slow:
            raise ValueError("fast period must be smaller than slow period")
        self.fast, self.slow, self.trend = fast, slow, trend

    @property
    def warmup(self) -> int:
        return max(self.slow, self.trend) + 1

    def params(self) -> dict[str, Any]:
        return {"fast": self.fast, "slow": self.slow, "trend": self.trend}

    def indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["ema_fast"] = ema(out["close"], self.fast)
        out["ema_slow"] = ema(out["close"], self.slow)
        # trend=0 disables the trend filter
        out["ema_trend"] = ema(out["close"], self.trend) if self.trend else 0.0
        return out

    def signal(self, history: Frame, ctx: StrategyContext) -> Signal:
        if len(history) < 2:
            return Signal.hold("not enough data")
        fast, slow = history["ema_fast"], history["ema_slow"]
        f_prev, f_now, s_prev, s_now = fast[-2], fast[-1], slow[-2], slow[-1]
        close, trend = history["close"][-1], history["ema_trend"][-1]

        if crossed_above(f_prev, f_now, s_prev, s_now):
            if close > trend:
                return Signal(Action.BUY, f"EMA{self.fast} crossed above EMA{self.slow} in uptrend")
            return Signal.hold("bullish cross but price below trend EMA")
        if crossed_below(f_prev, f_now, s_prev, s_now):
            return Signal(Action.SELL, f"EMA{self.fast} crossed below EMA{self.slow}")
        return Signal.hold()
