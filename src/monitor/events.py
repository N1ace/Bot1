"""Layer 1: scheduled macro events. Sets a no-new-entries window from T-24h to T+2h.

Sources (official, free): the BLS release calendar (.ics) for CPI and payrolls, and the
Federal Reserve's FOMC calendar page, which has no feed, so its known layout is parsed.
Times are New York local, stored as UTC: 08:30 for BLS, 14:00 on the last day of an FOMC
meeting. The offset comes from time zone rules, so the weeks when the US and Israel change
clocks on different dates need no special handling.

If a fetch fails, the saved dates stay in use and the owner is warned.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from monitor.flags import set_flag
from monitor.store import connect, iso, utc_now

log = logging.getLogger(__name__)

BLS_ICS = "https://www.bls.gov/schedule/news_release/bls.ics"
FOMC_PAGE = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
HEADERS = {"User-Agent": "Mozilla/5.0 (cryptobot news monitor)"}
NEW_YORK = ZoneInfo("America/New_York")
BLS_KINDS = {"Consumer Price Index": "CPI", "Employment Situation": "NFP"}
MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                          "nov", "dec"))}


def parse_bls_ics(text: str) -> list[tuple[str, datetime, str]]:
    """(kind, UTC time, title) for CPI and payrolls releases."""
    unfolded = text.replace("\r\n ", "").replace("\r\n", "\n")
    out = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", unfolded, flags=re.S):
        title = re.search(r"^SUMMARY:(.*)$", block, flags=re.M)
        start = re.search(r"^DTSTART[^:\n]*:(\d{8}T\d{6})", block, flags=re.M)
        if not title or not start or title.group(1).strip() not in BLS_KINDS:
            continue
        name = title.group(1).strip()
        local = datetime.strptime(start.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=NEW_YORK)
        out.append((BLS_KINDS[name], local.astimezone(timezone.utc), name))
    return out


def parse_fomc_html(html: str) -> list[tuple[str, datetime, str]]:
    """(FOMC, UTC time, title) for the decision day, the last day of each scheduled meeting."""
    out = []
    parts = re.split(r"(\d{4}) FOMC Meetings", html)
    for year, section in zip(parts[1::2], parts[2::2]):
        for month, days in re.findall(r'fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>.*?'
                                      r'fomc-meeting__date[^>]*>\s*([^<]+?)\s*<', section, flags=re.S):
            last_month = MONTHS.get(month.split("/")[-1].strip().lower()[:3])
            last_day = re.findall(r"\d+", days)
            if last_month is None or not last_day:
                continue
            local = datetime(int(year), last_month, int(last_day[-1]), 14, 0, tzinfo=NEW_YORK)
            out.append(("FOMC", local.astimezone(timezone.utc), "FOMC decision"))
    return out


def refresh(db: str | Path, session: requests.Session | None = None) -> list[str]:
    """Update saved dates. Returns problems for the owner; the old dates stay usable."""
    http = session or requests.Session()
    problems = []
    for name, url, parse, expected in (("BLS", BLS_ICS, parse_bls_ics, {"CPI", "NFP"}),
                                       ("FOMC", FOMC_PAGE, parse_fomc_html, {"FOMC"})):
        try:
            resp = http.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            rows = parse(resp.content.decode("utf-8-sig", errors="replace"))
            missing = expected - {k for k, _, _ in rows}
            if missing:
                raise ValueError(f"no {', '.join(sorted(missing))} dates found, the page layout may have changed")
        except (requests.RequestException, ValueError) as e:
            problems.append(f"{name} calendar could not be updated: {e}")
            continue
        with connect(db) as con:
            con.executemany("INSERT OR REPLACE INTO events VALUES (?,?,?)", [(k, iso(t), n) for k, t, n in rows])
    return problems


def upcoming(db: str | Path, now: datetime, hours: float) -> list[tuple[str, datetime, str]]:
    with connect(db) as con:
        rows = con.execute("SELECT kind, time_utc, title FROM events WHERE time_utc >= ? AND time_utc <= ? "
                           "ORDER BY time_utc", (iso(now), iso(now + timedelta(hours=hours)))).fetchall()
    return [(k, datetime.fromisoformat(t), n) for k, t, n in rows]


def apply_blackouts(db: str | Path, cfg: dict, now: datetime | None = None) -> list[str]:
    """Set no_new_entries_until for every event whose window is open now. Never closes positions."""
    now = now or utc_now()
    before, after = cfg["blackout_before_hours"], cfg["blackout_after_hours"]
    kinds = set(cfg["events"])
    opened = []
    with connect(db) as con:
        rows = con.execute("SELECT kind, time_utc, title FROM events WHERE time_utc >= ? AND time_utc <= ?",
                           (iso(now - timedelta(hours=after)), iso(now + timedelta(hours=before)))).fetchall()
    for kind, time_utc, title in rows:
        when = datetime.fromisoformat(time_utc)
        if kind in kinds and set_flag(db, "no_new_entries_until", when + timedelta(hours=after), "calendar",
                                      f"{title} at {when:%Y-%m-%d %H:%M} UTC", now=now):
            opened.append(f"{title} at {when:%Y-%m-%d %H:%M} UTC: no new entries until "
                          f"{when + timedelta(hours=after):%Y-%m-%d %H:%M} UTC")
    return opened


def latest_saved(db: str | Path) -> datetime | None:
    with connect(db) as con:
        row = con.execute("SELECT MAX(time_utc) FROM events").fetchone()
    return datetime.fromisoformat(row[0]) if row and row[0] else None


def running_out(db: str | Path, cfg: dict, now: datetime | None = None) -> list[str]:
    """Kinds of event with no date far enough ahead: their blackouts are about to stop working."""
    now = now or utc_now()
    limit = now + timedelta(days=cfg["min_days_ahead"])
    with connect(db) as con:
        latest = dict(con.execute("SELECT kind, MAX(time_utc) FROM events GROUP BY kind").fetchall())
    out = []
    for kind in cfg["events"]:
        last = datetime.fromisoformat(latest[kind]) if kind in latest else None
        if last is None or last < limit:
            out.append(f"Only {last:%Y-%m-%d} is saved as the last {kind} date. Blackouts stop after it, so check "
                       "the official calendar has newer dates." if last else f"No {kind} dates saved: no blackouts.")
    return out
