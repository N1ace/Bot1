"""SQLite record of every order, every fill and every risk event.

Why SQLite: it is a single file (data/cryptobot.sqlite), needs no server, ships
with Python, and is easy to export to CSV/Excel for an accountant.

For tax reporting (Israel) each fill stores: UTC time, pair, side, quantity,
price, quote value, fee and fee asset, plus which environment it happened in.
Converting values to ILS (Bank of Israel daily rates) will be a separate report
script; check the exact required format with your accountant.
"""

from __future__ import annotations

import json
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
CREATE TABLE IF NOT EXISTS stream_events (   -- every user-data-stream event, raw
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at     TEXT NOT NULL,          -- ISO 8601, UTC (our clock)
    environment     TEXT NOT NULL,
    event_type      TEXT NOT NULL,          -- ORDER_TRADE_UPDATE / ACCOUNT_UPDATE / ALGO_UPDATE / ...
    event_time      INTEGER,                -- Binance event time 'E', ms since epoch
    symbol          TEXT,
    payload         TEXT NOT NULL           -- the full JSON message
);
CREATE TABLE IF NOT EXISTS funding_payments (  -- futures funding, paid/received every 8h (tax)
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,          -- ISO 8601, UTC (Binance event time)
    environment     TEXT NOT NULL,
    symbol          TEXT,
    asset           TEXT NOT NULL,
    amount          REAL NOT NULL           -- negative = we paid, positive = we received
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
        self._migrate()
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

    def update_order(self, client_order_id: str, status: str, exchange_order_id: str | None = None,
                     note: str | None = None) -> None:
        """Update an order recorded earlier (looked up by our client order id)."""
        self._db.execute(
            "UPDATE orders SET status=?, exchange_order_id=COALESCE(?, exchange_order_id),"
            " note=COALESCE(?, note) WHERE client_order_id=?",
            (status, exchange_order_id, note, client_order_id),
        )
        self._db.commit()

    def record_fill(self, fill: Fill) -> None:
        self._db.execute(
            "INSERT INTO fills (order_id, timestamp, environment, symbol, side, quantity, price, quote_value,"
            " fee, fee_asset, realized_pnl, client_order_id, trade_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (fill.order_id, fill.timestamp.astimezone(timezone.utc).isoformat(), self.environment, fill.symbol,
             fill.side.value, fill.quantity, fill.price, fill.quote_value, fill.fee, fill.fee_asset,
             fill.realized_pnl, fill.client_order_id, fill.trade_id),
        )
        self._db.commit()

    def fills_for(self, client_order_ids: list[str]) -> list[dict]:
        marks = ",".join("?" * len(client_order_ids))
        return self._rows(f"SELECT * FROM fills WHERE client_order_id IN ({marks}) ORDER BY id", client_order_ids)

    def fills_since(self, symbol: str, since_iso: str) -> list[dict]:
        """All fills for a symbol at/after a UTC time - includes stop-triggered fills,
        whose client order id is chosen by Binance, not by us."""
        return self._rows("SELECT * FROM fills WHERE symbol=? AND environment=? AND timestamp>=? ORDER BY id",
                          [symbol, self.environment, since_iso])

    def _rows(self, sql: str, params) -> list[dict]:
        cur = self._db.execute(sql, params)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]

    def _migrate(self) -> None:
        """Add columns introduced after a database file was first created."""
        existing = {row[1] for row in self._db.execute("PRAGMA table_info(fills)")}
        for column, sql_type in (("realized_pnl", "REAL"), ("client_order_id", "TEXT"), ("trade_id", "TEXT")):
            if column not in existing:
                self._db.execute(f"ALTER TABLE fills ADD COLUMN {column} {sql_type}")
        self._db.commit()

    def record_stream_event(self, event: dict) -> None:
        """Store one user-data-stream event exactly as received."""
        # Order and algo events carry the symbol in o.s; funding ACCOUNT_UPDATEs in S.
        order = event.get("o")
        symbol = order.get("s") if isinstance(order, dict) else event.get("S")
        if event.get("e") == "listenKeyExpired":
            event = {k: v for k, v in event.items() if k != "listenKey"}  # keys are secrets, even expired
        self._db.execute(
            "INSERT INTO stream_events (received_at, environment, event_type, event_time, symbol, payload)"
            " VALUES (?,?,?,?,?,?)",
            (_now(), self.environment, event.get("e", "unknown"), event.get("E"), symbol, json.dumps(event)),
        )
        self._db.commit()

    def record_funding(self, event_time_ms: int, symbol: str | None, asset: str, amount: float) -> None:
        ts = datetime.fromtimestamp(event_time_ms / 1000, tz=timezone.utc).isoformat()
        self._db.execute(
            "INSERT INTO funding_payments (timestamp, environment, symbol, asset, amount) VALUES (?,?,?,?,?)",
            (ts, self.environment, symbol, asset, amount),
        )
        self._db.commit()

    def count_stream_events(self) -> dict[str, int]:
        rows = self._db.execute(
            "SELECT event_type, COUNT(*) FROM stream_events WHERE environment=? GROUP BY event_type",
            (self.environment,),
        ).fetchall()
        return dict(rows)

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
