"""The research tool must find a planted pattern and must NOT invent one in random data."""

from datetime import UTC, datetime, timedelta

import numpy as np

from tradebot.domain.models import Candle
from tradebot.research import patterns as p
from tradebot.research.run import run_research

T0 = datetime(2024, 1, 1, tzinfo=UTC)


def walk(n: int, seed: int, plant: bool = False) -> list[Candle]:
    """5-minute random walk. With plant=True: every sharp 1h drop is followed by a rebound."""
    rng = np.random.default_rng(seed)
    price, closes = 100.0, []
    rebound = 0
    for i in range(n):
        r = rng.normal(0, 0.002)
        if plant and i % 700 == 0 and i > 400:
            r = -0.03  # a sharp drop...
            rebound = 12
        elif rebound:
            r += 0.003  # ...followed by a reliable bounce over the next hour
            rebound -= 1
        price *= 1 + r
        closes.append(price)
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        hi, lo = max(prev, c) * 1.0005, min(prev, c) * 0.9995
        out.append(Candle(T0 + timedelta(minutes=5 * i), prev, hi, lo, c, 100.0))
        prev = c
    return out


def test_finds_planted_pattern():
    result = run_research({"X/USDT": walk(120_000, seed=1, plant=True)})
    found = {(r["pattern"], r["horizon"]) for r in result["coins"]["X/USDT"]["confirmed"]}
    assert ("sharp drop (-2% in 1h)", "1h") in found


def test_random_data_gives_no_confirmed_pattern():
    for seed in (2, 3):
        result = run_research({"X/USDT": walk(60_000, seed=seed)})
        assert result["coins"]["X/USDT"]["confirmed"] == []


def test_fee_hurdle_and_hourly_shapes():
    df = p.load(walk(20_000, seed=4))
    hurdle = p.fee_hurdle(df)
    assert list(hurdle.index) == ["15m", "1h", "4h"]
    assert (hurdle["median_abs_move_pct"].diff().dropna() > 0).all()  # longer = bigger moves
    assert len(p.hourly(df)) == 24 and len(p.entry_exit(df).columns) == 12
