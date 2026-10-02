"""Strategies built from other strategies (Composite pattern).

Each child computes its own indicators; we keep them side by side under a
separate column group so children never clash over column names.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from tradebot.domain.models import Action, Signal
from tradebot.domain.strategies.base import (
    Strategy,
    StrategyContext,
    StrategyFactory,
    register,
)
from tradebot.domain.strategies.frame import Frame
from tradebot.domain.strategies.indicators import adx

_OWN = "_self"  # column group for the composite's own indicators


def _child_frames(
    df: pd.DataFrame, children: dict[str, Strategy], own: pd.DataFrame
) -> pd.DataFrame:
    frames = {key: child.indicators(df) for key, child in children.items()}
    frames[_OWN] = own
    return pd.concat(frames, axis=1)


@register("regime_switch")
class RegimeSwitch(Strategy):
    description = (
        "Measures trend strength with ADX. Strong trend -> uses the trend strategy "
        "(default ema_cross); sideways market -> uses the range strategy (default rsi_reversion)."
    )
    param_grid = {"threshold": [20, 25, 30]}

    def __init__(
        self, trend: Strategy, range_: Strategy, adx_period: int = 14, threshold: float = 25
    ):
        self.children = {"trend": trend, "range": range_}
        self.adx_period, self.threshold = adx_period, threshold

    @classmethod
    def from_params(cls, params: dict[str, Any], factory: StrategyFactory) -> Strategy:
        trend = factory.create_from_spec(params.pop("trend", {"name": "ema_cross"}))
        range_ = factory.create_from_spec(params.pop("range", {"name": "rsi_reversion"}))
        return cls(trend, range_, **params)

    @property
    def warmup(self) -> int:
        return max(max(c.warmup for c in self.children.values()), self.adx_period * 3)

    def params(self) -> dict[str, Any]:
        return {
            "trend": repr(self.children["trend"]),
            "range": repr(self.children["range"]),
            "adx_period": self.adx_period,
            "threshold": self.threshold,
        }

    def indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        own = pd.DataFrame({"adx": adx(df, self.adx_period)}, index=df.index)
        return _child_frames(df, self.children, own)

    def describe(self, history: Frame) -> str:
        strength = history[(_OWN, "adx")][-1]
        regime = "trend" if strength >= self.threshold else "range"
        child = self.children[regime].describe(history.group(regime))
        return f"Trend strength (ADX) {strength:.0f} -> using the {regime} strategy. {child}"

    def signal(self, history: Frame, ctx: StrategyContext) -> Signal:
        strength = history[(_OWN, "adx")][-1]
        if pd.isna(strength):
            return Signal.hold("ADX warming up")
        regime = "trend" if strength >= self.threshold else "range"
        sig = self.children[regime].signal(history.group(regime), ctx)
        return Signal(sig.action, f"[{regime} regime, ADX {strength:.0f}] {sig.reason}".strip())


@register("ensemble")
class Ensemble(Strategy):
    description = (
        "Voting: asks several strategies and acts only when at least `min_votes` of them "
        "gave the same signal within the last `window` candles. Fewer, higher-quality trades."
    )
    param_grid = {"min_votes": [1, 2], "window": [3, 6]}

    def __init__(self, members: list[Strategy], min_votes: int = 2, window: int = 3):
        if not 1 <= min_votes <= len(members):
            raise ValueError("min_votes must be between 1 and the number of members")
        self.children = {f"m{i}_{m.name}": m for i, m in enumerate(members)}
        self.min_votes, self.window = min_votes, window

    @classmethod
    def from_params(cls, params: dict[str, Any], factory: StrategyFactory) -> Strategy:
        specs = params.pop(
            "members", [{"name": "ema_cross"}, {"name": "rsi_reversion"}, {"name": "breakout"}]
        )
        return cls([factory.create_from_spec(s) for s in specs], **params)

    @property
    def warmup(self) -> int:
        return max(c.warmup for c in self.children.values()) + self.window

    def params(self) -> dict[str, Any]:
        return {
            "members": [repr(c) for c in self.children.values()],
            "min_votes": self.min_votes,
            "window": self.window,
        }

    def indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        return _child_frames(df, self.children, pd.DataFrame(index=df.index))

    def _recent_actions(self, child: Strategy, frame: Frame, ctx: StrategyContext) -> list:
        """The child's signals on the last `window` candles, newest first."""
        n = len(frame)
        return [
            child.signal(frame.head(n - back), ctx).action
            for back in range(min(self.window, n - 1))
        ]

    def describe(self, history: Frame) -> str:
        parts = [c.describe(history.group(k)) for k, c in self.children.items()]
        return f"Needs {self.min_votes} of {len(parts)} members to agree. " + " | ".join(parts)

    def signal(self, history: Frame, ctx: StrategyContext) -> Signal:
        # Holding a coin -> only exits matter. Not holding -> only entries matter.
        action = Action.SELL if ctx.in_position else Action.BUY
        recent = {
            k: self._recent_actions(c, history.group(k), ctx) for k, c in self.children.items()
        }
        voters = [k for k, actions in recent.items() if action in actions]
        if len(voters) < self.min_votes:
            return Signal.hold()
        # Only act on a fresh signal: at least one voter must signal on THIS candle
        if not any(recent[k][:1] == [action] for k in voters):
            return Signal.hold()
        return Signal(action, f"{len(voters)} votes: {', '.join(voters)}")
