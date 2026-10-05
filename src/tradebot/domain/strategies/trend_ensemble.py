"""Strategy — ensemble trend following (Zarattini, Pagani & Barbon, 2025), small-account version.

Runs nine Donchian trend "voters" (look-backs of 5 to 360 candles; use DAILY candles).
Each voter is long from a new highest close until the close falls below its trailing
stop (the channel midpoint, which only moves up). The strategy holds the coin while
at least `min_votes` of the nine voters are long.

It is a STATE strategy: it says BUY whenever enough voters agree, so pressing Start
can enter an existing trend straight away. Exits come from the voters (no fixed
take-profit), so a winning trend can keep running.

Tested in research/trend_ensemble.py: on the 18 months after the paper was published
(unseen data), 5 coins, 0.2% fees: +13%, worst drop -16%.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from tradebot.domain.models import Action, Signal
from tradebot.domain.strategies.base import Strategy, StrategyContext, register
from tradebot.domain.strategies.frame import Frame

PAPER_LOOKBACKS = (5, 10, 20, 30, 60, 90, 150, 250, 360)


def voter(close: pd.Series, n: int) -> pd.Series:
    """1 while a Donchian(n) trend position would be held, else 0. Causal."""
    hi = close.rolling(n, min_periods=n).max()
    mid = (hi + close.rolling(n, min_periods=n).min()) / 2
    out, holding, stop = [], False, float("-inf")
    for c, h, m in zip(close.to_numpy(), hi.to_numpy(), mid.to_numpy(), strict=True):
        if h != h:  # NaN while warming up
            out.append(0.0)
            continue
        if holding and c <= stop:
            holding = False
        elif not holding and c >= h:
            holding, stop = True, m
        if holding:
            stop = max(stop, m)
        out.append(1.0 if holding else 0.0)
    return pd.Series(out, index=close.index)


@register("trend_ensemble")
class TrendEnsemble(Strategy):
    description = (
        "Nine trend voters (5 to 360 candles, use daily candles). Holds while at least "
        "`min_votes` of them are in an uptrend; each voter exits below its rising trailing stop."
    )
    param_grid = {"min_votes": [3, 5, 7]}

    def __init__(self, min_votes: int = 5, lookbacks: tuple[int, ...] = PAPER_LOOKBACKS) -> None:
        if not 1 <= min_votes <= len(lookbacks):
            raise ValueError("min_votes must be between 1 and the number of look-backs")
        self.min_votes, self.lookbacks = min_votes, tuple(lookbacks)

    @property
    def warmup(self) -> int:
        return max(self.lookbacks) + 1

    def params(self) -> dict[str, Any]:
        return {"min_votes": self.min_votes}

    def indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        votes = sum(voter(out["close"], n) for n in self.lookbacks)
        out["votes"] = votes
        return out

    def describe(self, history: Frame) -> str:
        v = int(history["votes"][-1])
        state = "enough for a position" if v >= self.min_votes else "not enough yet"
        return (
            f"{v} of {len(self.lookbacks)} trend voters are in an uptrend "
            f"(needs {self.min_votes}: {state}). Exits when fewer than {self.min_votes} remain."
        )

    def signal(self, history: Frame, ctx: StrategyContext) -> Signal:
        v = int(history["votes"][-1])
        if v >= self.min_votes:
            return Signal(Action.BUY, f"{v}/{len(self.lookbacks)} trend voters agree")
        if ctx.in_position:
            return Signal(Action.SELL, f"only {v}/{len(self.lookbacks)} trend voters left")
        return Signal.hold(f"{v}/{len(self.lookbacks)} voters, need {self.min_votes}")
