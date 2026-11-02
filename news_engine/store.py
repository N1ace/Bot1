"""SQLite storage and time helpers. Everything is stored in UTC; exchange time is used only for session logic."""

import os
import sqlite3
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

# Outside OneDrive, because file syncing and SQLite don't mix
DB_PATH = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ftmo-bot" / "news_engine.sqlite"
NEW_YORK = ZoneInfo("America/New_York")
SERVER_HOURS_AHEAD_OF_NY = 7  # FTMO's server clock, checked against the US open across 2022-2026

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY, type TEXT NOT NULL, tier INTEGER NOT NULL, scheduled_utc TEXT NOT NULL, source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reactions (
    event_id TEXT NOT NULL REFERENCES events(id), symbol TEXT NOT NULL, window TEXT NOT NULL, return_pct REAL NOT NULL,
    range_pct REAL NOT NULL, pre_vol_pct REAL, PRIMARY KEY (event_id, symbol, window)
);
CREATE TABLE IF NOT EXISTS news_items (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, title TEXT NOT NULL, published_utc TEXT, fetched_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS news_labels (
    news_id TEXT PRIMARY KEY, category TEXT NOT NULL, importance TEXT NOT NULL, model TEXT NOT NULL, created_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_summaries (day TEXT PRIMARY KEY, summary TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS llm_log (
    created_utc TEXT NOT NULL, model TEXT NOT NULL, prompt TEXT NOT NULL, response TEXT NOT NULL, input_tokens INTEGER,
    output_tokens INTEGER, cost_usd REAL
);
CREATE TABLE IF NOT EXISTS regime (day TEXT PRIMARY KEY, label TEXT NOT NULL, risk_budget_mult REAL NOT NULL, details_json TEXT);
CREATE TABLE IF NOT EXISTS blackouts (
    event_id TEXT NOT NULL, start_utc TEXT NOT NULL, end_utc TEXT NOT NULL, firm TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS predictions (
    event_id TEXT NOT NULL, symbol TEXT NOT NULL, window TEXT NOT NULL, q25 REAL, q50 REAL, q75 REAL,
    b25 REAL, b50 REAL, b75 REAL, n_past INTEGER, pre_vol_pct REAL, created_at TEXT NOT NULL,
    outcome REAL, loss REAL, base_loss REAL, PRIMARY KEY (event_id, symbol, window)
);
"""


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    return db


def new_york_to_utc(day: str, at: time) -> datetime:
    """A New York wall-clock time as UTC, correct on both sides of a clock change."""
    local = datetime.combine(datetime.fromisoformat(day).date(), at, tzinfo=NEW_YORK)
    return local.astimezone(timezone.utc)


def server_to_utc(times: pd.Series) -> pd.Series:
    """FTMO server stamps to UTC. The hour that repeats when clocks change is dropped, as it can't be placed."""
    new_york = times - pd.Timedelta(hours=SERVER_HOURS_AHEAD_OF_NY)
    return new_york.dt.tz_localize(NEW_YORK, ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")
