"""Late entry: also act on a BUY signal from the last few candles (Decorator pattern).

Normally a strategy only buys on the exact candle where its signal happens.
If the bot was started (or a trade just finished) a few candles later, that
signal is missed. LateEntry wraps ANY strategy and still allows the buy if:

  - the signal happened within the last `window` candles,
  - no SELL signal came after it, and
  - the price is still within `max_atr` x ATR of the signal candle's close
    (it hasn't run away, and it hasn't collapsed either).

That price rule also stops re-buying the same old signal after a stop-loss
(price fell far) or a take-profit (price rose far).
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from tradebot.domain.models import Action, Signal
from tradebot.domain.strategies.base import Strategy, StrategyContext
from tradebot.domain.strategies.frame import Frame
from tradebot.domain.strategies.indicators import atr

_INNER, _OWN = "inner", "_late"


class LateEntry(Strategy):
    def __init__(self, inner: Strategy, window: int = 3, max_atr: float = 1.0) -> None:
        if window < 0 or max_atr <= 0:
            raise ValueError("late entry window must be >= 0 and max_atr > 0")
        self.inner, self.window, self.max_atr = inner, window, max_atr
        self.name = inner.name  # reports keep the strategy's own name
        self.description = inner.description

    @property
    def warmup(self) -> int:
        return self.inner.warmup + self.window

    def params(self) -> dict[str, Any]:
        return {
            **self.inner.params(),
            "late_entry_window": self.window,
            "late_entry_max_atr": self.max_atr,
        }

    def indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        own = pd.DataFrame({"atr": atr(df, 14), "close": df["close"]}, index=df.index)
        return pd.concat({_INNER: self.inner.indicators(df), _OWN: own}, axis=1)

    def signal(self, history: Frame, ctx: StrategyContext) -> Signal:
        inner = history.group(_INNER)
        now = self.inner.signal(inner, ctx)
        if now.action is not Action.HOLD or ctx.in_position or self.window == 0:
            return now
        closes, last_atr = history[(_OWN, "close")], history[(_OWN, "atr")][-1]
        n = len(inner)
        for back in range(1, min(self.window, n - 2) + 1):
            past = self.inner.signal(inner.head(n - back), ctx)
            if past.action is Action.SELL:
                return now  # the idea was cancelled since
            if past.action is Action.BUY:
                drift = abs(closes[-1] - closes[-1 - back])
                if drift <= self.max_atr * last_atr:
                    return Signal(Action.BUY, f"late entry ({back} candles ago): {past.reason}")
                return Signal.hold(f"missed signal {back} candles ago, price moved too far")
        return now

    def describe(self, history: Frame) -> str:
        return self.inner.describe(history.group(_INNER))
