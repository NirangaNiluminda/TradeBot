"""A tiny, fast, read-only table that strategies read in `signal()`.

pandas is great for computing indicators over a whole history at once, but slow
at "give me the last value" thousands of times per backtest. `Frame` holds the
same columns as plain NumPy arrays, and slicing it only creates cheap views.

    history["close"][-1]    # latest close
    history["ema_fast"][-2] # previous candle's fast EMA
    history.group("trend")  # sub-table of a composite strategy's child
"""

from __future__ import annotations

from collections.abc import Hashable

import numpy as np
import pandas as pd


class Frame:
    __slots__ = ("_cols", "_len")

    def __init__(self, columns: dict[Hashable, np.ndarray], length: int) -> None:
        self._cols = columns
        self._len = length

    @classmethod
    def from_dataframe(cls, df: pd.DataFrame) -> Frame:
        cols = {c: df[c].to_numpy(dtype=float, copy=False) for c in df.columns}
        return cls(cols, len(df))

    def __len__(self) -> int:
        return self._len

    def __getitem__(self, column: Hashable) -> np.ndarray:
        return self._cols[column]

    def __contains__(self, column: Hashable) -> bool:
        return column in self._cols

    @property
    def columns(self) -> list[Hashable]:
        return list(self._cols)

    def slice(self, start: int, stop: int) -> Frame:
        """Rows [start, stop) — like `list[start:stop]` (negative numbers allowed)."""
        start, stop, _ = slice(start, stop).indices(self._len)
        stop = max(stop, start)
        return Frame({c: a[start:stop] for c, a in self._cols.items()}, stop - start)

    def head(self, n: int) -> Frame:
        """The first `n` rows (used to look at 'how things were n candles ago')."""
        return self.slice(0, n)

    def tail(self, n: int) -> Frame:
        return self.slice(max(0, self._len - n), self._len)

    def group(self, key: Hashable) -> Frame:
        """Columns stored as (key, name) tuples, returned with plain names."""
        sub = {c[1]: a for c, a in self._cols.items() if isinstance(c, tuple) and c[0] == key}
        if not sub:
            raise KeyError(f"no column group {key!r}")
        return Frame(sub, self._len)
