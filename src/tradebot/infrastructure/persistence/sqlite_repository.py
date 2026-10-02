"""SQLite storage for positions and trades (Repository pattern).

SQLite is a single file (e.g. data/tradebot_testnet.sqlite) — no server needed.
Swapping to Postgres later means writing another class with the same methods.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from tradebot.domain.models import ExitReason, Position, Trade

_SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY,
    amount REAL NOT NULL,
    entry_price REAL NOT NULL,
    entry_time TEXT NOT NULL,
    entry_fee REAL NOT NULL,
    stop_loss REAL NOT NULL,
    take_profit REAL NOT NULL,
    strategy TEXT NOT NULL,
    exit_order_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,
    entry_time TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_time TEXT NOT NULL,
    exit_price REAL NOT NULL,
    amount REAL NOT NULL,
    fees REAL NOT NULL,
    exit_reason TEXT NOT NULL,
    pnl REAL NOT NULL
);
"""


class SqliteTradeRepository:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        # The dashboard reads from a web worker thread, the bot writes from the main one:
        # allow both, and serialise access with a lock.
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._db.executescript(_SCHEMA)

    def save_position(self, p: Position) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?,?,?,?)",
                (p.symbol, p.amount, p.entry_price, p.entry_time.isoformat(), p.entry_fee,
                 p.stop_loss, p.take_profit, p.strategy, p.exit_order_id),
            )  # fmt: skip

    def delete_position(self, symbol: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))

    def load_positions(self) -> list[Position]:
        with self._lock:
            rows = self._db.execute(
                "SELECT symbol, amount, entry_price, entry_time, entry_fee, stop_loss, take_profit,"
                " strategy, exit_order_id FROM positions"
            ).fetchall()
        return [
            Position(r[0], r[1], r[2], datetime.fromisoformat(r[3]), r[4], r[5], r[6], r[7], r[8])
            for r in rows
        ]

    def save_trade(self, t: Trade) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO trades (symbol, strategy, entry_time, entry_price, exit_time,"
                " exit_price, amount, fees, exit_reason, pnl) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (t.symbol, t.strategy, t.entry_time.isoformat(), t.entry_price,
                 t.exit_time.isoformat(), t.exit_price, t.amount, t.fees, t.exit_reason.value,
                 t.pnl),
            )  # fmt: skip

    def load_trades(self) -> list[Trade]:
        with self._lock:
            rows = self._db.execute(
                "SELECT symbol, strategy, entry_time, entry_price, exit_time, exit_price, amount,"
                " fees, exit_reason FROM trades ORDER BY id"
            ).fetchall()
        return [
            Trade(r[0], r[1], datetime.fromisoformat(r[2]), r[3], datetime.fromisoformat(r[4]),
                  r[5], r[6], r[7], ExitReason(r[8]))
            for r in rows
        ]  # fmt: skip

    def close(self) -> None:
        self._db.close()
