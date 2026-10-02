"""Technical indicators.

Every function here is *causal*: the value at row i only uses rows 0..i.
That is what makes backtests honest (no peeking into the future).
pandas is used purely as a maths tool here.
"""

from __future__ import annotations

import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average — an average price that weights recent candles more."""
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average — plain average of the last `period` values."""
    return series.rolling(period, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (0-100), Wilder's method.

    Below 30: price fell a lot recently ("oversold"). Above 70: rose a lot ("overbought").
    """
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    out = 100 - 100 / (1 + rs)
    # No losses at all in the window -> maximum strength
    return out.where(avg_loss != 0, 100.0)


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range — how much the price typically moves per candle (volatility)."""
    return true_range(df).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index (0-100) — how strong the trend is, not its direction.

    Roughly: above 25 = trending market, below 20 = sideways market.
    """
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    atr_ = atr(df, period)
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr_
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def donchian_high(high: pd.Series, period: int) -> pd.Series:
    """Highest high of the PREVIOUS `period` candles (excludes the current one)."""
    return high.rolling(period, min_periods=period).max().shift(1)


def donchian_low(low: pd.Series, period: int) -> pd.Series:
    """Lowest low of the PREVIOUS `period` candles (excludes the current one)."""
    return low.rolling(period, min_periods=period).min().shift(1)


def crossed_above(a_prev: float, a_now: float, b_prev: float, b_now: float) -> bool:
    """True if line A was at/below line B on the previous candle and is above it now."""
    return a_prev <= b_prev and a_now > b_now


def crossed_below(a_prev: float, a_now: float, b_prev: float, b_now: float) -> bool:
    return a_prev >= b_prev and a_now < b_now
