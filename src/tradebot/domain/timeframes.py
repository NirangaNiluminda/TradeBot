"""Helpers for timeframe strings like '15m', '1h', '4h', '1d'."""

from datetime import timedelta

_UNITS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


def timeframe_seconds(timeframe: str) -> int:
    try:
        return int(timeframe[:-1]) * _UNITS[timeframe[-1]]
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Unknown timeframe '{timeframe}'. Use e.g. 15m, 1h, 4h, 1d") from exc


def timeframe_delta(timeframe: str) -> timedelta:
    return timedelta(seconds=timeframe_seconds(timeframe))
