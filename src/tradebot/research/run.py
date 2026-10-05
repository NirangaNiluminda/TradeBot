"""Runs every analysis for every coin and both periods, and saves the results as JSON."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from tradebot.domain.models import Candle
from tradebot.research import patterns as p


def _records(df: pd.DataFrame) -> Any:
    clean = df.reset_index().replace({math.inf: None, -math.inf: None})
    return json.loads(clean.to_json(orient="records"))


def run_research(candles: dict[str, list[Candle]]) -> dict[str, Any]:
    out: dict[str, Any] = {"coins": {}}
    for symbol, series in candles.items():
        df = p.load(series)
        parts = p.split(df)
        coin: dict[str, Any] = {
            "candles": len(df),
            "start": df.index[0].isoformat(),
            "split": parts.confirmation.index[0].isoformat(),
            "end": df.index[-1].isoformat(),
            "fee_hurdle": _records(p.fee_hurdle(df)),
        }
        for period, part in (("discovery", parts.discovery), ("confirmation", parts.confirmation)):
            coin[period] = {
                "hourly": _records(p.hourly(part)),
                "weekday": _records(p.weekday(part)),
                "events": _records(p.event_study(part)),
                "baseline": _records(p.baseline(part)),
                "entry_exit": _records(p.entry_exit(part)),
                "buy_and_hold_pct": float(
                    (part["close"].iloc[-1] / part["open"].iloc[0] - 1) * 100
                ),
            }
        d, c = parts.discovery, parts.confirmation
        coin["confirmed"] = _records(
            p.confirmed_patterns(p.event_study(d), p.event_study(c), p.baseline(d), p.baseline(c))
        )
        out["coins"][symbol] = coin
    return out


def save(result: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=1))
    return path
