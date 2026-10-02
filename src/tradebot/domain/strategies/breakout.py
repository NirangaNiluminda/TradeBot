"""Strategy 3 — Donchian channel breakout confirmed by volume."""

from __future__ import annotations

from typing import Any

import pandas as pd

from tradebot.domain.models import Action, Signal
from tradebot.domain.strategies.base import Strategy, StrategyContext, register
from tradebot.domain.strategies.frame import Frame
from tradebot.domain.strategies.indicators import donchian_high, donchian_low, sma


@register("breakout")
class Breakout(Strategy):
    description = (
        "BUY when price closes above the highest high of the last N candles with "
        "above-average volume. SELL when price closes below the lowest low of the last M candles."
    )
    param_grid = {"entry_period": [20, 40], "exit_period": [10, 20], "volume_mult": [1.0, 1.5]}

    def __init__(
        self, entry_period: int = 20, exit_period: int = 10, volume_mult: float = 1.5
    ) -> None:
        self.entry_period, self.exit_period, self.volume_mult = (
            entry_period,
            exit_period,
            volume_mult,
        )

    @property
    def warmup(self) -> int:
        return max(self.entry_period, self.exit_period) + 1

    def params(self) -> dict[str, Any]:
        return {
            "entry_period": self.entry_period,
            "exit_period": self.exit_period,
            "volume_mult": self.volume_mult,
        }

    def indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["upper"] = donchian_high(out["high"], self.entry_period)
        out["lower"] = donchian_low(out["low"], self.exit_period)
        # average volume of the PREVIOUS candles, so the breakout candle doesn't inflate it
        out["avg_volume"] = sma(out["volume"], self.entry_period).shift(1)
        return out

    def signal(self, history: Frame, ctx: StrategyContext) -> Signal:
        close, volume = history["close"][-1], history["volume"][-1]
        if close < history["lower"][-1]:
            return Signal(Action.SELL, f"closed below {self.exit_period}-candle low")
        if close > history["upper"][-1]:
            if volume > self.volume_mult * history["avg_volume"][-1]:
                return Signal(
                    Action.BUY, f"breakout above {self.entry_period}-candle high on volume"
                )
            return Signal.hold("breakout without volume confirmation")
        return Signal.hold()
