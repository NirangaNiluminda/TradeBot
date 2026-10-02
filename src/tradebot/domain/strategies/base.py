"""Strategy interface + registry (Strategy, Registry and Factory patterns).

How a strategy works, in two steps:

1. `indicators(df)`  — takes a table of candles and adds indicator columns
                       (EMA, RSI, ...). Computed in one fast, vectorised pass.
2. `signal(history)` — looks at the indicator table *up to the current candle*
                       (a fast read-only `Frame`) and returns BUY / SELL / HOLD.

Because `signal` only ever receives rows up to "now", a strategy cannot
accidentally use future prices — in backtest or live.

To add a new strategy: create a file in this folder, subclass `Strategy`, and
decorate it with `@register("my_name")`. It then appears in the CLI and config.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar, TypeVar

import pandas as pd

from tradebot.domain.models import RiskAssessment, Signal
from tradebot.domain.strategies.frame import Frame

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """Extra information a strategy may use besides prices (e.g. news risk later)."""

    in_position: bool = False
    risk: RiskAssessment = RiskAssessment()


class Strategy(ABC):
    """Base class every trading strategy implements."""

    name: ClassVar[str] = "unnamed"
    description: ClassVar[str] = ""
    # Settings the optimizer tries: {"param": [value, value, ...]}. Keep it small —
    # every extra option multiplies the work and the risk of fitting to noise.
    param_grid: ClassVar[dict[str, list[Any]]] = {}

    @property
    @abstractmethod
    def warmup(self) -> int:
        """How many candles are needed before indicators become valid."""

    @abstractmethod
    def indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Return a copy of `df` (OHLCV columns, time index) with indicator columns added."""

    @abstractmethod
    def signal(self, history: Frame, ctx: StrategyContext) -> Signal:
        """Decide using `history` — indicator rows up to and including the current candle.

        Read values like `history["close"][-1]` (latest) or `history["close"][-2]` (previous).
        """

    def params(self) -> dict[str, Any]:
        """Current parameters (for reports and the dashboard)."""
        return {}

    def describe(self, history: Frame) -> str:
        """Plain words: what is this strategy waiting for right now? (dashboard / LLM)"""
        return ""

    @classmethod
    def from_params(cls, params: dict[str, Any], factory: StrategyFactory) -> Strategy:
        """Build from config. Composite strategies override this to build their children."""
        return cls(**params)

    def __repr__(self) -> str:
        args = ", ".join(f"{k}={v}" for k, v in self.params().items())
        return f"{self.name}({args})"


S = TypeVar("S", bound=type[Strategy])


class StrategyFactory:
    """Registry of available strategies; builds them by name from config."""

    def __init__(self) -> None:
        self._registry: dict[str, type[Strategy]] = {}

    def register(self, name: str) -> Callable[[S], S]:
        def decorator(cls: S) -> S:
            if name in self._registry:
                raise ValueError(f"Strategy '{name}' registered twice")
            cls.name = name
            self._registry[name] = cls
            return cls

        return decorator

    def create(self, name: str, params: dict[str, Any] | None = None) -> Strategy:
        """Build a strategy. Two optional extra params work for every strategy:
        `late_entry_window` and `late_entry_max_atr` (see late_entry.py)."""
        try:
            cls = self._registry[name]
        except KeyError:
            known = ", ".join(sorted(self._registry))
            raise ValueError(f"Unknown strategy '{name}'. Available: {known}") from None
        params = dict(params or {})
        window = params.pop("late_entry_window", None)
        max_atr = params.pop("late_entry_max_atr", 1.0)
        strategy = cls.from_params(params, self)
        if window:
            from tradebot.domain.strategies.late_entry import LateEntry

            strategy = LateEntry(strategy, int(window), float(max_atr))
        return strategy

    def create_from_spec(self, spec: dict[str, Any]) -> Strategy:
        """Build from a nested config dict: {name: ..., params: {...}}."""
        return self.create(spec["name"], spec.get("params"))

    def available(self) -> dict[str, type[Strategy]]:
        return dict(sorted(self._registry.items()))


# The default registry used by the app. Strategy modules register themselves here.
strategy_factory = StrategyFactory()
register = strategy_factory.register
