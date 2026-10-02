"""Supplies the engine with "what the market looks like at the current candle".

Two implementations of the same idea:

- `PrecomputedFeed` (backtests): computes indicators over the whole history once,
  then hands out a slice ending at the current candle. Fast.
- `RollingFeed` (live trading): keeps the most recent candles in memory and
  recomputes indicators when a new candle closes.

Both give the strategy the same view, so the strategy code is identical in
backtest and live. Indicators are causal, so precomputing cannot leak the future.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from tradebot.application.frames import candles_to_frame
from tradebot.domain.models import Candle
from tradebot.domain.strategies.base import Strategy
from tradebot.domain.strategies.frame import Frame
from tradebot.domain.strategies.indicators import atr

# signal() only needs the most recent rows; indicators already contain the long history.
SIGNAL_LOOKBACK = 50
# Volatility measure used for stop-loss distances (Average True Range over 14 candles).
ATR_PERIOD = 14


@dataclass(frozen=True, slots=True)
class MarketView:
    history: Frame  # strategy indicators, rows up to and including the current candle
    atr: float  # typical price move per candle right now (NaN while warming up)


class IndicatorFeed(Protocol):
    def update(self, symbol: str, candle: Candle) -> MarketView:
        """Register the newly closed candle and return the view ending at it."""
        ...


class PrecomputedFeed:
    def __init__(self, strategy: Strategy, candles: dict[str, list[Candle]]) -> None:
        self._frames: dict[str, Frame] = {}
        self._atr: dict[str, np.ndarray] = {}
        self._positions: dict[str, dict] = {}
        for symbol, series in candles.items():
            df = candles_to_frame(series)
            self._frames[symbol] = Frame.from_dataframe(strategy.indicators(df))
            self._atr[symbol] = atr(df, ATR_PERIOD).to_numpy()
            self._positions[symbol] = {t: i for i, t in enumerate(df.index)}

    def update(self, symbol: str, candle: Candle) -> MarketView:
        i = self._positions[symbol][candle.time]
        history = self._frames[symbol].slice(max(0, i + 1 - SIGNAL_LOOKBACK), i + 1)
        return MarketView(history, float(self._atr[symbol][i]))


class RollingFeed:
    def __init__(self, strategy: Strategy, max_candles: int | None = None) -> None:
        self.strategy = strategy
        # Keep plenty of extra history so slow EMAs converge to the same values as in backtests.
        self.max_candles = max_candles or max(1000, strategy.warmup * 5)
        self._candles: dict[str, deque[Candle]] = {}

    def seed(self, symbol: str, candles: list[Candle]) -> None:
        """Load recent history at startup so the strategy can trade immediately."""
        self._candles[symbol] = deque(candles, maxlen=self.max_candles)

    def candles(self, symbol: str) -> list[Candle]:
        return list(self._candles.get(symbol, ()))

    def update(self, symbol: str, candle: Candle) -> MarketView:
        buf = self._candles.setdefault(symbol, deque(maxlen=self.max_candles))
        if not buf or candle.time > buf[-1].time:
            buf.append(candle)
        df = candles_to_frame(list(buf))
        history = Frame.from_dataframe(self.strategy.indicators(df)).tail(SIGNAL_LOOKBACK)
        last_atr = atr(df, ATR_PERIOD).iloc[-1] if len(df) else math.nan
        return MarketView(history, float(last_atr))
