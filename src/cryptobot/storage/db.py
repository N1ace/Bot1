"""SQLite record of every order, every fill and every risk event.

Why SQLite: it is a single file (data/cryptobot.sqlite), needs no server, ships
with Python, and is easy to export to CSV/Excel for an accountant.

For tax reporting (Israel) each fill stores: UTC time, pair, side, quantity,
price, quote value, fee and fee asset, plus which environment it happened in.
Converting values to ILS (Bank of Israel daily rates) will be a separate report
script; check the exact required format with your accountant.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from cryptobot.models import Fill, OrderRequest

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL,          -- ISO 8601, UTC
    environment     TEXT NOT NULL,          -- paper / testnet / live
    mode            TEXT NOT NULL,          -- spot / margin / futures / copytrade_lead
    account         TEXT NOT NULL,
    exchange_order_id TEXT,
    client_order_id TEXT,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    order_type      TEXT NOT NULL,
    quantity        REAL NOT NULL,
    price           REAL,
    stop_loss       REAL,
    status          TEXT NOT NULL,          -- requested / rejected / open / filled / canceled
    strategy        TEXT,
    note            TEXT                    -- e.g. risk rejection reasons
);
CREATE TABLE IF NOT EXISTS fills (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id        TEXT NOT NULL,
    timestamp       TEXT NOT NULL,          -- ISO 8601, UTC
    environment     TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    quantity        REAL NOT NULL,
    price           REAL NOT NULL,
    quote_value     REAL NOT NULL,
    fee             REAL NOT NULL,
    fee_asset       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS risk_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    event           TEXT NOT NULL,          -- e.g. HALT
    detail          TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TradeStore:
    def __init__(self, path: str | Path, environment: str, mode: str, account: str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.executescript(SCHEMA)
        self.environment, self.mode, self.account = environment, mode, account

    def record_order(self, order: OrderRequest, status: str, exchange_order_id: str | None = None,
                     client_order_id: str | None = None, note: str = "") -> int:
        cur = self._db.execute(
            "INSERT INTO orders (created_at, environment, mode, account, exchange_order_id, client_order_id,"
            " symbol, side, order_type, quantity, price, stop_loss, status, strategy, note)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (_now(), self.environment, self.mode, self.account, exchange_order_id, client_order_id,
             order.symbol, order.side.value, order.order_type, order.quantity, order.price,
             order.stop_loss, status, order.strategy, note),
        )
        self._db.commit()
        return cur.lastrowid

    def record_fill(self, fill: Fill) -> None:
        self._db.execute(
            "INSERT INTO fills (order_id, timestamp, environment, symbol, side, quantity, price, quote_value,"
            " fee, fee_asset) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (fill.order_id, fill.timestamp.astimezone(timezone.utc).isoformat(), self.environment, fill.symbol,
             fill.side.value, fill.quantity, fill.price, fill.quote_value, fill.fee, fill.fee_asset),
        )
        self._db.commit()

    def record_halt(self, reason: str) -> None:
        self._db.execute("INSERT INTO risk_events (timestamp, event, detail) VALUES (?,?,?)", (_now(), "HALT", reason))
        self._db.commit()

    def last_halt_date(self) -> date | None:
        """Date (UTC) of the most recent halt, so a restart keeps today's halt in force."""
        row = self._db.execute(
            "SELECT timestamp FROM risk_events WHERE event='HALT' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return datetime.fromisoformat(row[0]).date() if row else None

    def close(self) -> None:
        self._db.close()
