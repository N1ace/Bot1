"""SQLite storage for the monitor. Its own file, so it never mixes with any bot's trade log."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import yaml

SCHEMA = """
CREATE TABLE IF NOT EXISTS risk_flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flag TEXT NOT NULL,             -- no_new_entries_until / vol_mult / block_adds_direction / kill_switch
    value TEXT NOT NULL,
    set_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,       -- ISO 8601 UTC
    expires_at TEXT                 -- NULL only for kill_switch, which waits for the owner
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
    kind TEXT NOT NULL,             -- CPI / NFP / FOMC
    time_utc TEXT NOT NULL,
    title TEXT NOT NULL,
    PRIMARY KEY (kind, time_utc)
);
CREATE TABLE IF NOT EXISTS liq_hours (
    hour_utc TEXT PRIMARY KEY,      -- start of the hour
    notional REAL NOT NULL          -- USDT liquidated; undercounts, Binance pushes only the largest per second
);
CREATE TABLE IF NOT EXISTS news_items (
    url TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    published_utc TEXT NOT NULL,
    fetched_utc TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0 -- 1 once it went into a briefing
);
CREATE TABLE IF NOT EXISTS news_briefings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_utc TEXT NOT NULL,
    kind TEXT NOT NULL,             -- ai / raw
    text TEXT NOT NULL,
    data TEXT                       -- validated JSON from the model
);
CREATE TABLE IF NOT EXISTS llm_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_utc TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt TEXT NOT NULL,
    response TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_utc TEXT NOT NULL,
    key TEXT NOT NULL,
    text TEXT NOT NULL
);
"""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat()


def load_settings(path: str | Path = "config/monitor.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


@contextmanager
def connect(db: str | Path):
    """One connection per use: the scheduler and the WebSocket thread share the same file."""
    if str(db) != ":memory:":
        Path(db).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db, timeout=30)
    try:
        con.executescript(SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


def get_kv(db: str | Path, key: str, default: str | None = None) -> str | None:
    with connect(db) as con:
        row = con.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_kv(db: str | Path, key: str, value: str) -> None:
    with connect(db) as con:
        con.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, value))
