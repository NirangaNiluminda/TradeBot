from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from tradebot.domain.models import Candle, SymbolRules

T0 = datetime(2025, 1, 1, tzinfo=UTC)
HOUR = timedelta(hours=1)


def candle(i: int, close: float, *, open_=None, high=None, low=None, volume=100.0) -> Candle:
    o = close if open_ is None else open_
    return Candle(
        T0 + i * HOUR,
        o,
        high if high is not None else max(o, close),
        low if low is not None else min(o, close),
        close,
        volume,
    )


def random_walk(n: int = 1500, seed: int = 7, start: float = 100.0) -> list[Candle]:
    rng = np.random.default_rng(seed)
    closes = start * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    out, prev = [], start
    for i, c in enumerate(closes):
        wiggle = abs(rng.normal(0, 0.004)) * c
        out.append(
            Candle(
                T0 + i * HOUR,
                prev,
                max(prev, c) + wiggle,
                min(prev, c) - wiggle,
                float(c),
                float(rng.uniform(50, 150)),
            )
        )
        prev = float(c)
    return out


@pytest.fixture
def btc_rules() -> SymbolRules:
    return SymbolRules("BTC/USDT", min_notional=5.0, amount_step=0.00001, min_amount=0.00001)


@pytest.fixture
def unit_rules() -> SymbolRules:
    """Rules for a cheap made-up coin, priced around 100."""
    return SymbolRules("XYZ/USDT", min_notional=5.0, amount_step=0.001, min_amount=0.001)
