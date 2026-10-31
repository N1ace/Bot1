"""SQLite storage for the advisor: daily runs (level gates), tips, suggestions, small settings."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS observer_runs (
    day TEXT PRIMARY KEY,               -- UTC date of the data the run covered (last closed bar)
    ran_at TEXT NOT NULL,
    status TEXT NOT NULL,               -- ok / data_problems / failed
    problems TEXT                       -- JSON list
);
CREATE TABLE IF NOT EXISTS tips (
    day TEXT PRIMARY KEY,               -- max one tip per day
    concept TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,                 -- level_up / raise_risk / parameter / re_enable / other
    title TEXT NOT NULL,
    evidence TEXT NOT NULL,
    config_change TEXT,                 -- the exact line the owner would edit
    status TEXT NOT NULL DEFAULT 'pending',   -- pending / approved / rejected
    decided_at TEXT
);
CREATE TABLE IF NOT EXISTS advisor_kv (key TEXT PRIMARY KEY, value TEXT);
"""

MAX_SUGGESTIONS_PER_WEEK = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AdvisorStore:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.executescript(SCHEMA)

    # ---- runs and the Level 0 gate ------------------------------------------------

    def record_run(self, day: date, status: str, problems: list[str]) -> None:
        self._db.execute("INSERT OR REPLACE INTO observer_runs VALUES (?,?,?,?)",
                         (day.isoformat(), _now(), status, json.dumps(problems)))
        self._db.commit()

    def clean_streak(self, day: date) -> int:
        """Consecutive days with a clean run ('ok'), ending at `day`. A missing day breaks it."""
        rows = dict(self._db.execute("SELECT day, status FROM observer_runs").fetchall())
        streak, d = 0, day
        while rows.get(d.isoformat()) == "ok":
            streak += 1
            d -= timedelta(days=1)
        return streak

    def runs_between(self, first: date, last: date) -> list[tuple[str, str]]:
        return self._db.execute("SELECT day, status FROM observer_runs WHERE day >= ? AND day <= ? ORDER BY day",
                                (first.isoformat(), last.isoformat())).fetchall()

    # ---- tips ---------------------------------------------------------------------

    def tip_for(self, day: date) -> tuple[str, str] | None:
        row = self._db.execute("SELECT concept, text FROM tips WHERE day=?", (day.isoformat(),)).fetchone()
        return tuple(row) if row else None

    def record_tip(self, day: date, concept: str, text: str) -> None:
        self._db.execute("INSERT OR REPLACE INTO tips VALUES (?,?,?)", (day.isoformat(), concept, text))
        self._db.commit()

    def concepts_seen(self) -> set[str]:
        return {r[0] for r in self._db.execute("SELECT DISTINCT concept FROM tips")}

    def recent_concepts(self, day: date, days: int) -> list[str]:
        since = (day - timedelta(days=days)).isoformat()
        return [r[0] for r in self._db.execute("SELECT concept FROM tips WHERE day >= ? AND day < ? ORDER BY day",
                                               (since, day.isoformat()))]

    # ---- suggestions (owner approval flow) ------------------------------------------

    def add_suggestion(self, kind: str, title: str, evidence: str, config_change: str | None) -> int | None:
        """Store a suggestion unless the weekly limit (3) is used up or the same one is still pending."""
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        count = self._db.execute("SELECT COUNT(*) FROM suggestions WHERE created_at >= ?", (week_ago,)).fetchone()[0]
        dup = self._db.execute("SELECT 1 FROM suggestions WHERE title=? AND status='pending'", (title,)).fetchone()
        if count >= MAX_SUGGESTIONS_PER_WEEK or dup:
            return None
        cur = self._db.execute("INSERT INTO suggestions (created_at, kind, title, evidence, config_change)"
                               " VALUES (?,?,?,?,?)", (_now(), kind, title, evidence, config_change))
        self._db.commit()
        return cur.lastrowid

    def suggestion(self, sid: int) -> dict | None:
        cur = self._db.execute("SELECT * FROM suggestions WHERE id=?", (sid,))
        row = cur.fetchone()
        return dict(zip([d[0] for d in cur.description], row)) if row else None

    def pending(self) -> list[dict]:
        cur = self._db.execute("SELECT * FROM suggestions WHERE status='pending' ORDER BY id")
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]

    def decide(self, sid: int, approved: bool) -> dict | None:
        """Record the owner's answer. Does NOT apply anything - the owner edits the config."""
        s = self.suggestion(sid)
        if s is None or s["status"] != "pending":
            return s
        self._db.execute("UPDATE suggestions SET status=?, decided_at=? WHERE id=?",
                         ("approved" if approved else "rejected", _now(), sid))
        self._db.commit()
        return self.suggestion(sid)

    # ---- small settings ---------------------------------------------------------------

    def get(self, key: str, default: str | None = None) -> str | None:
        row = self._db.execute("SELECT value FROM advisor_kv WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set(self, key: str, value: str) -> None:
        self._db.execute("INSERT OR REPLACE INTO advisor_kv VALUES (?,?)", (key, value))
        self._db.commit()

    def close(self) -> None:
        self._db.close()
