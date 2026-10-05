"""Short-term pattern research on historical candles (Step 1: analyse before training).

Every question is answered TWICE: on a "discovery" period (older 60% of the data)
and on a "confirmation" period (newer 40%). A pattern that only shows up in one
of them is most likely luck. Returns are for BUYING (spot can't short) and are
shown NET of the 0.2% round-trip fee unless said otherwise.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from tradebot.application.frames import candles_to_frame
from tradebot.domain.models import Candle
from tradebot.domain.strategies.indicators import ema, rsi

FEE_PCT = 0.2  # Binance 0.1% to buy + 0.1% to sell
LOCAL_TZ = "Asia/Colombo"  # Sri Lanka time, for the "best hour" tables
HORIZONS = {"15m": 3, "1h": 12, "4h": 48}  # in 5-minute candles
DISCOVERY_SHARE = 0.6


@dataclass(frozen=True)
class Split:
    discovery: pd.DataFrame
    confirmation: pd.DataFrame


def load(candles: list[Candle]) -> pd.DataFrame:
    df = candles_to_frame(candles)
    df.index = pd.to_datetime(df.index, utc=True)
    return df


def split(df: pd.DataFrame) -> Split:
    cut = df.index[int(len(df) * DISCOVERY_SHARE)]
    return Split(df[df.index < cut], df[df.index >= cut])


def _t_stat(x: pd.Series) -> float:
    n = len(x)
    if n < 3 or x.std() == 0:
        return 0.0
    return float(x.mean() / (x.std() / math.sqrt(n)))


# ------------------------------------------------------------------ 1. time of day / week
def hourly(df: pd.DataFrame) -> pd.DataFrame:
    """Gross % move during each local hour (buying at the start, selling at the end)."""
    h = df["close"].resample("1h").last().dropna()
    o = df["open"].resample("1h").first().reindex(h.index)
    ret = (h / o - 1) * 100
    local = ret.index.tz_convert(LOCAL_TZ)
    rng = ((df["high"].resample("1h").max() / df["low"].resample("1h").min() - 1) * 100).reindex(
        h.index
    )
    out = pd.DataFrame({"ret": ret.values, "range": rng.values}, index=local.hour)
    g = out.groupby(level=0)
    return pd.DataFrame(
        {
            "mean_move_pct": g["ret"].mean(),
            "up_share_pct": g["ret"].apply(lambda x: (x > 0).mean() * 100),
            "avg_range_pct": g["range"].mean(),
            "t": g["ret"].apply(_t_stat),
            "n": g["ret"].size(),
        }
    ).rename_axis("hour_local")


def weekday(df: pd.DataFrame) -> pd.DataFrame:
    d = df["close"].resample("1D").last().dropna()
    o = df["open"].resample("1D").first().reindex(d.index)
    ret = pd.Series(((d / o - 1) * 100).values, index=d.index.day_name())
    g = ret.groupby(level=0)
    order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    return pd.DataFrame(
        {
            "mean_move_pct": g.mean(),
            "up_share_pct": g.apply(lambda x: (x > 0).mean() * 100),
            "t": g.apply(_t_stat),
            "n": g.size(),
        }
    ).reindex(order)


# ------------------------------------------------------------------ 2. fee hurdle
def fee_hurdle(df: pd.DataFrame) -> pd.DataFrame:
    """How big are typical moves over 15m / 1h / 4h compared with the 0.2% fee?"""
    rows = []
    for name, n in HORIZONS.items():
        move = (df["close"].shift(-n) / df["close"] - 1).dropna() * 100
        rows.append(
            {
                "horizon": name,
                "median_abs_move_pct": float(move.abs().median()),
                "up_more_than_fee_pct": float((move > FEE_PCT).mean() * 100),
                "up_more_than_0.5_pct": float((move > 0.5).mean() * 100),
                "down_more_than_0.5_pct": float((move < -0.5).mean() * 100),
            }
        )
    return pd.DataFrame(rows).set_index("horizon")


# ------------------------------------------------------------------ 3. pattern scan
def events(df: pd.DataFrame) -> dict[str, pd.Series]:
    """Common short-term setups, each a True/False per 5-minute candle (past data only)."""
    c, v = df["close"], df["volume"]
    ret_1h = c / c.shift(12) - 1
    vol_avg = v.rolling(288, min_periods=288).mean().shift(1)  # previous 24h
    high_24h = df["high"].rolling(288, min_periods=288).max().shift(1)
    low_24h = df["low"].rolling(288, min_periods=288).min().shift(1)
    rsi_1h = rsi(c, 14 * 12)  # roughly a 14-hour RSI on 5m data
    trend_up = c > ema(c, 200 * 12)  # above the ~200-hour average
    candle_up = c > df["open"]
    return {
        "volume spike, green candle": (v > 3 * vol_avg) & candle_up,
        "volume spike, red candle": (v > 3 * vol_avg) & ~candle_up,
        "sharp drop (-2% in 1h)": ret_1h < -0.02,
        "sharp rise (+2% in 1h)": ret_1h > 0.02,
        "RSI oversold (<30)": rsi_1h < 30,
        "RSI overbought (>70)": rsi_1h > 70,
        "new 24h high": c > high_24h,
        "new 24h low": c < low_24h,
        "dip in uptrend (-1% in 1h, above 200h avg)": (ret_1h < -0.01) & trend_up,
    }


def _first_of_clusters(mask: pd.Series, gap: int) -> pd.Series:
    """Keep only the first signal of each burst (otherwise one event counts 12 times)."""
    idx = np.flatnonzero(mask.fillna(False).to_numpy())
    keep, last = [], -(10**9)
    for i in idx:
        if i - last >= gap:
            keep.append(i)
            last = i
    out = pd.Series(False, index=mask.index)
    out.iloc[keep] = True
    return out


def event_study(df: pd.DataFrame) -> pd.DataFrame:
    """After each setup, what happened over the next 15m / 1h / 4h? (net of fees)"""
    rows = []
    for name, mask in events(df).items():
        hits = _first_of_clusters(mask, gap=12)
        for h_name, n in HORIZONS.items():
            fwd = (df["close"].shift(-n) / df["close"] - 1) * 100 - FEE_PCT
            x = fwd[hits].dropna()
            rows.append(
                {
                    "pattern": name,
                    "horizon": h_name,
                    "n": len(x),
                    "mean_net_pct": float(x.mean()) if len(x) else math.nan,
                    "win_rate_pct": float((x > 0).mean() * 100) if len(x) else math.nan,
                    "t": _t_stat(x),
                }
            )
    return pd.DataFrame(rows)


def baseline(df: pd.DataFrame) -> pd.DataFrame:
    """Buying at a RANDOM moment: the bar every pattern must beat."""
    rows = []
    for h_name, n in HORIZONS.items():
        x = ((df["close"].shift(-n) / df["close"] - 1) * 100 - FEE_PCT).dropna()
        rows.append({"horizon": h_name, "mean_net_pct": float(x.mean()),
                     "win_rate_pct": float((x > 0).mean() * 100)})  # fmt: skip
    return pd.DataFrame(rows).set_index("horizon")


# ------------------------------------------------------------------ 4. entry hour x holding time
def entry_exit(df: pd.DataFrame, max_hold: int = 12) -> pd.DataFrame:
    """Average NET % for buying at local hour H and selling K hours later."""
    h = df["close"].resample("1h").last().dropna()
    o = df["open"].resample("1h").first().reindex(h.index)
    hours = h.index.tz_convert(LOCAL_TZ).hour
    table = {}
    for k in range(1, max_hold + 1):
        net = (h.shift(-(k - 1)) / o - 1) * 100 - FEE_PCT
        table[k] = pd.Series(net.values, index=hours).groupby(level=0).mean()
    return pd.DataFrame(table).rename_axis("entry_hour_local")


# ------------------------------------------------------------------ verdicts
def confirmed_patterns(
    disc: pd.DataFrame,
    conf: pd.DataFrame,
    base_disc: pd.DataFrame,
    base_conf: pd.DataFrame,
    min_n: int = 30,
    min_t: float = 2.0,
) -> pd.DataFrame:
    """Patterns that held up in BOTH periods: profitable after fees, better than buying at a
    random moment, and statistically strong (t >= 2), with enough examples.

    Being positive in two periods is NOT enough on its own: on pure random data some of the
    ~27 pattern/horizon combinations per coin pass that test by luck.
    """
    m = disc.merge(conf, on=["pattern", "horizon"], suffixes=("_disc", "_conf"))
    m["base_disc"] = m["horizon"].map(base_disc["mean_net_pct"])
    m["base_conf"] = m["horizon"].map(base_conf["mean_net_pct"])
    ok = (
        (m["n_disc"] >= min_n) & (m["n_conf"] >= min_n)
        & (m["mean_net_pct_disc"] > 0) & (m["mean_net_pct_conf"] > 0)
        & (m["mean_net_pct_disc"] > m["base_disc"]) & (m["mean_net_pct_conf"] > m["base_conf"])
        & (m["t_disc"] >= min_t) & (m["t_conf"] >= min_t)
    )  # fmt: skip
    return m[ok].sort_values("mean_net_pct_conf", ascending=False)
