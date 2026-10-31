"""SQLite storage for the monitor. Its own file, so it never mixes with any bot's trade log."""

from __future__ import annotations

import os
import socket
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


def validate_settings(s: dict) -> None:
    """Refuse settings that would quietly weaken a safety rule."""
    stress, gap = s["stress"], s["gap_risk"]
    checks = {
        "stable_min_venues must be at least 2": stress["stable_min_venues"] >= 2,
        "need 0.5 < stable_kill < stable_depeg < 1": 0.5 < stress["stable_kill"] < stress["stable_depeg"] < 1,
        "stable_confirm_seconds can't be negative": stress["stable_confirm_seconds"] >= 0,
        "dvol_vol_mult must be between 0 and 1": 0 <= stress["dvol_vol_mult"] <= 1,
        "stop_fill_multiple must be at least 1": gap["stop_fill_multiple"] >= 1,
        "max_gap_loss_per_position must be 0-5%": 0 < gap["max_gap_loss_per_position"] <= 0.05,
        "blackout hours can't be negative": min(s["calendar"]["blackout_before_hours"],
                                                s["calendar"]["blackout_after_hours"]) >= 0,
        "monitor_down_block_hours must be 0-2": 0 < s["failsafe"]["monitor_down_block_hours"] <= 2,
    }
    bad = [text for text, ok in checks.items() if not ok]
    if bad:
        raise ValueError("config/monitor.yaml: " + "; ".join(bad))


def load_settings(path: str | Path = "config/monitor.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        settings = yaml.safe_load(f)
    settings["database"] = os.path.expandvars(settings["database"])
    validate_settings(settings)
    return settings


class AlreadyRunning(RuntimeError):
    pass


def single_instance(port: int = 47631) -> socket.socket:
    """Only one monitor at a time: two would double alerts and hide each other's failures.
    Holding a local port works everywhere, and Windows frees it if the process dies."""
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", port))
    except OSError as e:
        lock.close()
        raise AlreadyRunning("The monitor is already running") from e
    return lock


@contextmanager
def connect(db: str | Path):
    """One connection per use: the scheduler and the WebSocket thread share the same file."""
    if str(db) != ":memory:":
        Path(db).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db, timeout=30)
    try:
        con.execute("PRAGMA journal_mode=WAL")    # a reading bot and the writing monitor don't block each other
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
