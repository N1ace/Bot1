"""Risk flags: the one way the monitor talks to a trading bot (news-event-layer-spec.md section 6).

Tighten only. A flag can block entries, shrink new positions, block adds in one direction,
or ask the bot to close everything. Nothing here can raise risk: `vol_mult` above 1 is
refused, and when several flags apply the most cautious one wins.

News and the AI summariser have no route in: `set_flag` only accepts the writers in
WRITERS, and summariser.py doesn't import this module (tests/test_monitor.py checks that).

Fail-safe: if the monitor's heartbeat is older than `monitor_down_block_hours`, or there has
never been one, `read_view` blocks new entries.

A bot calls `read_view()` before every entry and on every reconciliation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from monitor.store import connect, get_kv, iso, set_kv, utc_now

WRITERS = {"calendar", "stress", "owner"}
FLAGS = {"no_new_entries_until", "vol_mult", "block_adds_direction", "kill_switch"}
DIRECTIONS = {"long", "short"}
HEARTBEAT_KEY = "heartbeat_utc"


@dataclass(frozen=True)
class RiskView:
    """Everything a bot needs, already merged to the most cautious value."""
    no_new_entries_until: datetime | None = None
    vol_mult: float = 1.0
    blocked_add_directions: frozenset[str] = frozenset()
    kill_switch: bool = False
    monitor_down: bool = False
    reasons: tuple[str, ...] = field(default=())

    def entries_allowed(self, now: datetime) -> bool:
        if self.kill_switch or self.monitor_down:
            return False
        return self.no_new_entries_until is None or now >= self.no_new_entries_until

    def adds_allowed(self, side: str, now: datetime) -> bool:
        return self.entries_allowed(now) and side not in self.blocked_add_directions


def set_flag(db: str | Path, flag: str, value: str | float | datetime | bool, set_by: str, reason: str,
             hours: float | None = None, now: datetime | None = None) -> bool:
    """Store a flag. Returns False if the same flag is already active. Raises on anything invalid."""
    now = now or utc_now()
    if set_by not in WRITERS:
        raise ValueError(f"{set_by!r} may not set risk flags")
    if flag not in FLAGS:
        raise ValueError(f"unknown flag {flag!r}")
    expires: datetime | None = now + timedelta(hours=hours) if hours is not None else None
    if flag == "no_new_entries_until":
        if not isinstance(value, datetime):
            raise ValueError("no_new_entries_until needs a timestamp")
        expires, value = value, iso(value)
    elif flag == "vol_mult":
        value = float(value)
        if not 0.0 <= value <= 1.0:              # also refuses NaN
            raise ValueError(f"vol_mult must be 0-1, got {value}: flags can never increase risk")
        value = str(value)
    elif flag == "block_adds_direction":
        if value not in DIRECTIONS:
            raise ValueError(f"block_adds_direction must be long or short, got {value!r}")
    else:
        if value is not True:
            raise ValueError("kill_switch can only be set to True; clearing is clear_kill_switch()")
        value, expires = "true", None
    if flag != "kill_switch" and expires is None:
        raise ValueError(f"{flag} must expire")
    with connect(db) as con:
        active = con.execute("SELECT expires_at FROM risk_flags WHERE flag=? AND value=? AND "
                             "(expires_at IS NULL OR expires_at > ?)", (flag, value, iso(now))).fetchall()
        if any(row[0] is None or (expires and row[0] >= iso(expires)) for row in active):
            return False
        con.execute("INSERT INTO risk_flags (flag, value, set_by, reason, created_at, expires_at) VALUES (?,?,?,?,?,?)",
                    (flag, value, set_by, reason[:500], iso(now), iso(expires) if expires else None))
    return True


def clear_kill_switch(db: str | Path, reason: str, now: datetime | None = None) -> int:
    """Owner only: ends the kill switch by expiring it, so the record stays."""
    now = now or utc_now()
    with connect(db) as con:
        cur = con.execute("UPDATE risk_flags SET expires_at=?, reason=reason || ' | cleared by owner: ' || ? "
                          "WHERE flag='kill_switch' AND expires_at IS NULL", (iso(now), reason[:200]))
        return cur.rowcount


def beat(db: str | Path, now: datetime | None = None) -> None:
    set_kv(db, HEARTBEAT_KEY, iso(now or utc_now()))


def read_view(db: str | Path, now: datetime | None = None, down_after_hours: float = 2.0) -> RiskView:
    """All active flags merged to the most conservative value. Expired flags are ignored."""
    now = now or utc_now()
    with connect(db) as con:
        rows = con.execute("SELECT flag, value, reason FROM risk_flags WHERE expires_at IS NULL OR expires_at > ?",
                           (iso(now),)).fetchall()
    until, vol, blocked, kill, reasons = None, 1.0, set(), False, []
    for flag, value, reason in rows:
        reasons.append(f"{flag}: {reason}")
        if flag == "no_new_entries_until":
            ts = datetime.fromisoformat(value)
            until = ts if until is None else max(until, ts)
        elif flag == "vol_mult":
            vol = min(vol, float(value))
        elif flag == "block_adds_direction":
            blocked.add(value)
        elif flag == "kill_switch":
            kill = True
    last = get_kv(db, HEARTBEAT_KEY)
    down = last is None or now - datetime.fromisoformat(last) > timedelta(hours=down_after_hours)
    if down:
        reasons.append("monitor silent: new entries blocked (fail-safe)")
    return RiskView(until, vol, frozenset(blocked), kill, down, tuple(reasons))
