"""Ensemble trend following, after Zarattini, Pagani & Barbon (2025), "Catching Crypto Trends".

The method, on DAILY candles:
  - For each look-back n in {5, 10, 20, 30, 60, 90, 150, 250, 360} days:
      * enter (signal = 1) when today's close is the highest close of the last n days
      * exit when the close falls to/below a trailing stop; the stop starts at the
        channel midpoint and only ever moves UP: stop = max(stop, midpoint)
  - Size each signal so the position targets a fixed yearly volatility:
      weight_n = min(target_vol / volatility_90d, cap) * signal_n
  - The final weight is the AVERAGE over the 9 look-backs (an "ensemble"), so the
    position grows step by step as more trends agree, instead of all-in / all-out.
  - Only trade a volatility-driven size change if it is more than 20% off target
    (signal changes always trade at once). This cuts fees.

Our changes for Binance spot: cap = 100% (no borrowing; the paper allows 200%) and a
0.1% fee on every unit traded (0.2% round trip), never ignored.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

LOOKBACKS = (5, 10, 20, 30, 60, 90, 150, 250, 360)
PAPER_PUBLISHED = pd.Timestamp("2025-04-04", tz="UTC")  # data after this is out-of-sample


@dataclass(frozen=True)
class TrendConfig:
    lookbacks: tuple[int, ...] = LOOKBACKS
    target_vol: float = 0.25  # yearly volatility target per coin
    vol_window: int = 90  # days used to measure volatility
    cap: float = 1.0  # max position as a share of the coin's capital (spot: 100%)
    rebalance_threshold: float = 0.20  # ignore sizing drifts smaller than 20%
    fee: float = 0.001  # per unit traded (Binance 0.1%)


def donchian_signal(close: pd.Series, n: int) -> pd.Series:
    """1 while in a trend position for look-back n, else 0 (uses data up to each day only)."""
    c = close.to_numpy(dtype=float)
    up = close.rolling(n, min_periods=n).max().to_numpy()
    mid = (close.rolling(n, min_periods=n).max() + close.rolling(n, min_periods=n).min()) / 2
    mid = mid.to_numpy()
    pos = np.zeros(len(c))
    holding, stop = False, -math.inf
    for t in range(len(c)):
        if math.isnan(up[t]):
            continue
        if holding and c[t] <= stop:
            holding = False  # closed below the trailing stop
        elif not holding and c[t] >= up[t]:
            holding, stop = True, mid[t]  # new highest close: enter, stop at midpoint
        if holding:
            stop = max(stop, mid[t])  # stop for tomorrow never moves down
        pos[t] = 1.0 if holding else 0.0
    return pd.Series(pos, index=close.index)


def target_weights(close: pd.Series, cfg: TrendConfig) -> tuple[pd.Series, pd.DataFrame]:
    """Ensemble weight per day (0..cap) and the 0/1 signal of each look-back."""
    vol = close.pct_change().rolling(cfg.vol_window, min_periods=cfg.vol_window).std()
    vol = vol * math.sqrt(365)
    size = (cfg.target_vol / vol).clip(upper=cfg.cap).fillna(0.0)
    sigs = pd.DataFrame({n: donchian_signal(close, n) for n in cfg.lookbacks})
    return (sigs.mul(size, axis=0)).mean(axis=1), sigs


@dataclass
class TrendResult:
    equity: pd.Series  # growth of 1.0
    weight: pd.Series  # held position (share of capital)
    turnover: pd.Series  # units traded each day
    fees: float = 0.0
    trades: int = 0  # number of days with a buy or sell
    meta: dict = field(default_factory=dict)


def simulate(close: pd.Series, cfg: TrendConfig) -> TrendResult:
    """Daily simulation: decide at the close, hold from the next day. Fees on every change."""
    target, sigs = target_weights(close, cfg)
    signal_changed = sigs.diff().abs().sum(axis=1) > 0
    ret = close.pct_change().fillna(0.0).to_numpy()
    tgt = target.to_numpy()
    held = np.zeros(len(tgt))
    turnover = np.zeros(len(tgt))
    equity = np.ones(len(tgt))
    h = 0.0
    for t in range(len(tgt)):
        if t > 0:
            equity[t] = equity[t - 1] * (1 + h * ret[t])
            h = h * (1 + ret[t]) / (1 + h * ret[t]) if h else 0.0  # weight drifts with price
        w = tgt[t]
        drift_big = w == 0 or h == 0 or abs(w - h) > cfg.rebalance_threshold * max(w, h)
        if signal_changed.iloc[t] or (drift_big and abs(w - h) > 1e-12):
            turnover[t] = abs(w - h)
            equity[t] *= 1 - cfg.fee * turnover[t]
            h = w
        held[t] = h
    idx = close.index
    return TrendResult(
        equity=pd.Series(equity, idx),
        weight=pd.Series(held, idx),
        turnover=pd.Series(turnover, idx),
        fees=float((turnover * cfg.fee).sum()),
        trades=int((turnover > 1e-9).sum()),
    )


def portfolio(closes: dict[str, pd.Series], cfg: TrendConfig) -> TrendResult:
    """Equal capital per coin; each coin's slice follows its own ensemble weight."""
    parts = {s: simulate(c, cfg) for s, c in closes.items()}
    idx = sorted(set().union(*(p.equity.index for p in parts.values())))
    rets = pd.DataFrame({s: p.equity.pct_change() for s, p in parts.items()}).reindex(idx)
    port_ret = rets.mean(axis=1, skipna=True).fillna(0.0)  # coins join when their data starts
    w = pd.DataFrame({s: p.weight for s, p in parts.items()}).reindex(idx).mean(axis=1)
    return TrendResult(
        equity=(1 + port_ret).cumprod(),
        weight=w,
        turnover=pd.DataFrame({s: p.turnover for s, p in parts.items()}).reindex(idx).sum(axis=1),
        fees=sum(p.fees for p in parts.values()) / len(parts),
        trades=sum(p.trades for p in parts.values()),
        meta={"per_coin": parts},
    )


def stats(equity: pd.Series, weight: pd.Series | None = None) -> dict[str, float]:
    e = equity.dropna()
    days = (e.index[-1] - e.index[0]).days or 1
    r = e.pct_change().dropna()
    cagr = e.iloc[-1] ** (365 / days) - 1
    vol = r.std() * math.sqrt(365)
    dd = (e / e.cummax() - 1).min()
    out = {
        "total_pct": (e.iloc[-1] / e.iloc[0] - 1) * 100,
        "cagr_pct": cagr * 100,
        "vol_pct": vol * 100,
        "sharpe": (r.mean() * 365) / vol if vol else 0.0,
        "max_drawdown_pct": dd * 100,
    }
    if weight is not None:
        wt = weight.reindex(e.index)
        out["avg_exposure_pct"] = float(wt.mean() * 100)
        out["time_in_market_pct"] = float((wt > 0).mean() * 100)
    return out
