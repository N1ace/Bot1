"""News: read RSS feeds, drop repeats, keep what's recent. No AI and no cost.

Pages are never scraped: RSS is free, structured and stable. The link is the ID, and titles
that are nearly the same from several outlets are grouped into one item.
"""

from __future__ import annotations

import calendar
import html
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path

import feedparser
import requests

from monitor.store import connect, iso, utc_now

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (cryptobot news monitor)"}
SUMMARY_CHARS = 300


@dataclass
class NewsItem:
    url: str
    source: str
    title: str
    summary: str
    published: datetime
    also_in: list[str] = field(default_factory=list)


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def parse_feed(source: str, content: bytes) -> list[NewsItem]:
    out = []
    for entry in feedparser.parse(content).entries:
        url, title = entry.get("link"), clean(entry.get("title", ""))
        stamp = entry.get("published_parsed") or entry.get("updated_parsed")
        if not url or not title or not stamp:
            continue
        published = datetime.fromtimestamp(calendar.timegm(stamp), tz=timezone.utc)
        out.append(NewsItem(url, source, title, clean(entry.get("summary", ""))[:SUMMARY_CHARS], published))
    return out


def group_similar(items: list[NewsItem], threshold: float = 0.8) -> list[NewsItem]:
    """Keep the first of each set of nearly identical titles, noting the other outlets."""
    kept: list[NewsItem] = []
    for item in sorted(items, key=lambda i: i.published):
        twin = next((k for k in kept if SequenceMatcher(None, k.title.lower(), item.title.lower()).ratio() >= threshold),
                    None)
        if twin is None:
            kept.append(item)
        elif item.source != twin.source and item.source not in twin.also_in:
            twin.also_in.append(item.source)
    return kept


def fetch_all(feeds: dict[str, str], http: requests.Session | None = None) -> tuple[list[NewsItem], list[str]]:
    """(items, problems). One feed failing doesn't stop the others."""
    http = http or requests.Session()
    items, problems = [], []
    for source, url in feeds.items():
        try:
            resp = http.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            items += parse_feed(source, resp.content)
        except requests.RequestException as e:
            problems.append(f"{source} feed failed: {e}")
    return items, problems


def store_new(db: str | Path, items: list[NewsItem], now: datetime | None = None) -> int:
    now = now or utc_now()
    with connect(db) as con:
        before = con.execute("SELECT COUNT(*) FROM news_items").fetchone()[0]
        con.executemany("INSERT OR IGNORE INTO news_items (url, source, title, summary, published_utc, fetched_utc) "
                        "VALUES (?,?,?,?,?,?)",
                        [(i.url, i.source, i.title, i.summary, iso(i.published), iso(now)) for i in items])
        return con.execute("SELECT COUNT(*) FROM news_items").fetchone()[0] - before


def fresh_unused(db: str | Path, lookback_hours: float, max_items: int, now: datetime | None = None) -> list[NewsItem]:
    """Recent items not yet in a briefing, repeats grouped, newest kept if there are too many."""
    now = now or utc_now()
    with connect(db) as con:
        rows = con.execute("SELECT url, source, title, summary, published_utc FROM news_items WHERE used=0 AND "
                           "published_utc >= ? ORDER BY published_utc", (iso(now - timedelta(hours=lookback_hours)),)
                           ).fetchall()
    items = [NewsItem(u, s, t, x, datetime.fromisoformat(p)) for u, s, t, x, p in rows]
    return group_similar(items)[-max_items:]


def mark_used(db: str | Path, urls: list[str]) -> None:
    with connect(db) as con:
        con.executemany("UPDATE news_items SET used=1 WHERE url=?", [(u,) for u in urls])
