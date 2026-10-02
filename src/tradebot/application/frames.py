from __future__ import annotations

import pandas as pd

from tradebot.domain.models import Candle
from tradebot.domain.strategies.base import OHLCV_COLUMNS


def candles_to_frame(candles: list[Candle]) -> pd.DataFrame:
    """Convert candles into a pandas table indexed by candle open time."""
    df = pd.DataFrame(
        [(c.time, c.open, c.high, c.low, c.close, c.volume) for c in candles],
        columns=["time", *OHLCV_COLUMNS],
    )
    return df.set_index("time")
