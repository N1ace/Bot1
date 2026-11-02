"""Blackout schedule and the plain-text daily briefing. Times are stored in UTC and shown in Israel time."""

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from news_engine import store

ISRAEL = ZoneInfo("Asia/Jerusalem")
BRIEFING_PATH = store.DB_PATH.with_name("news_briefing.txt")

# Minutes before and after. The default is stricter than any firm rule found, and suits a CFD's wider news spreads
RULES = {"default": (6, 15), "FTMO funded": (2, 2)}
BLACKOUT_TYPES = ("CPI", "NFP", "FOMC", "PPI")
HEADLINES = 8


def write_blackouts(db, now: datetime, days: int = 30) -> int:
    """Rebuild the upcoming schedule. A future bot could read this table instead of hardcoded dates."""
    db.execute("DELETE FROM blackouts")
    rows = []
    end = now + timedelta(days=days)
    for event_id, scheduled in db.execute("SELECT id, scheduled_utc FROM events WHERE scheduled_utc > ? AND scheduled_utc < ?",
                                          (now.isoformat(), end.isoformat())).fetchall():
        if event_id.split("-")[0] not in BLACKOUT_TYPES:
            continue
        moment = pd.Timestamp(scheduled)
        for firm, (before, after) in RULES.items():
            rows.append((event_id, (moment - pd.Timedelta(minutes=before)).isoformat(),
                         (moment + pd.Timedelta(minutes=after)).isoformat(), firm))
    db.executemany("INSERT INTO blackouts VALUES (?, ?, ?, ?)", rows)
    db.commit()
    return len(rows)


def israel_time(utc_text: str) -> str:
    return pd.Timestamp(utc_text).tz_convert(ISRAEL).strftime("%a %d %b %H:%M")


def make_briefing(db, now: datetime, days: int = 7) -> str:
    lines = [f"News briefing, {pd.Timestamp(now).tz_convert(ISRAEL):%A %d %B %H:%M} Israel time", "",
             "Coming events (information only, not a trade signal):"]
    upcoming = db.execute("SELECT id, type, tier, scheduled_utc FROM events WHERE scheduled_utc > ? AND scheduled_utc < ? "
                          "ORDER BY scheduled_utc", (now.isoformat(), (now + timedelta(days=days)).isoformat())).fetchall()
    for event_id, kind, tier, scheduled in upcoming:
        lines.append(f"  {israel_time(scheduled)}  {kind} (tier {tier})")
        for symbol, low, mid, high, past in db.execute(
                "SELECT symbol, q25, q50, q75, n_past FROM predictions WHERE event_id = ? AND window = '1h' ORDER BY symbol",
                (event_id,)).fetchall():
            lines.append(f"      {symbol}: 1h move likely {low:.2f}-{high:.2f}%, median {mid:.2f}%, from {past} past events")
    if not upcoming:
        lines.append("  None")

    regime = db.execute("SELECT label, risk_budget_mult, details_json FROM regime ORDER BY day DESC LIMIT 1").fetchone()
    if regime:
        details = json.loads(regime[2])
        lines += ["", f"Market conditions: {regime[0]}, suggested risk {regime[1]:.2f}x (reduce-only, not used by any bot yet)",
                  f"  VIX {details['vix']}, 10y-2y curve {details['curve_10y_2y']}, S&P above 200-day: {details['sp500_above_200_day']}"]

    lines += ["", "Blackouts, default rule:"]
    windows = db.execute("SELECT event_id, start_utc, end_utc FROM blackouts WHERE firm = 'default' AND start_utc < ? "
                         "ORDER BY start_utc", ((now + timedelta(days=days)).isoformat(),)).fetchall()
    lines += [f"  {event}: {israel_time(start)} to {israel_time(end)[-5:]}" for event, start, end in windows] or ["  None"]

    summary = db.execute("SELECT summary FROM ai_summaries ORDER BY day DESC LIMIT 1").fetchone()
    if summary:
        lines += ["", "AI summary (may contain errors, not advice):", f"  {summary[0]}"]
    rows = db.execute("""SELECT l.importance, l.category, n.source, n.title FROM news_labels l JOIN news_items n ON n.id = l.news_id
                         WHERE l.category != 'ignored' AND l.created_utc >= ?
                         GROUP BY LOWER(n.title)
                         ORDER BY CASE l.importance WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END LIMIT ?""",
                      ((now - timedelta(days=1)).isoformat(), HEADLINES)).fetchall()
    if rows:
        lines += ["", "Headlines that matter:"]
        lines += [f"  {importance.upper()} {category} [{source}] {title}" for importance, category, source, title in rows]
    else:
        lines += ["", "Latest headlines, not sorted:"]
        news = db.execute("SELECT source, title FROM news_items ORDER BY COALESCE(published_utc, fetched_utc) DESC LIMIT ?",
                          (HEADLINES,)).fetchall()
        lines += [f"  [{source}] {title}" for source, title in news] or ["  None"]
    return "\n".join(lines)
