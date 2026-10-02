import pandas as pd
import pytest

from tradebot.domain.strategies import indicators as ind


def test_ema_matches_manual_formula():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    alpha = 2 / (3 + 1)
    expected = [1.0]
    for x in s[1:]:
        expected.append(alpha * x + (1 - alpha) * expected[-1])
    result = ind.ema(s, 3)
    assert result.isna().sum() == 2  # first values hidden until warmed up
    assert result.iloc[-1] == pytest.approx(expected[-1])


def test_rsi_stays_between_0_and_100():
    s = pd.Series([100, 101, 99, 102, 98, 103, 97, 104, 96, 105] * 5, dtype=float)
    r = ind.rsi(s, 14).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_rsi_is_100_when_price_only_rises():
    s = pd.Series(range(1, 40), dtype=float)
    assert ind.rsi(s, 14).iloc[-1] == 100


def test_donchian_excludes_current_candle():
    high = pd.Series([1.0, 2.0, 3.0, 10.0])
    # the channel at the last row must only see the previous 3 highs
    assert ind.donchian_high(high, 3).iloc[-1] == 3.0


def test_cross_helpers():
    assert ind.crossed_above(1, 3, 2, 2)
    assert not ind.crossed_above(3, 4, 2, 2)
    assert ind.crossed_below(3, 1, 2, 2)
