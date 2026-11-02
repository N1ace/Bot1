"""Log headlines from free RSS feeds. They can't be rebuilt later, so collection starts before any AI reads them.

Headlines are untrusted text from the internet: they are stored as data, and nothing here acts on them.
"""

import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

FEEDS = {
    "federal_reserve": "https://www.federalreserve.gov/feeds/press_all.xml",
    "ecb": "https://www.ecb.europa.eu/rss/press.html",
    "bea": "https://apps.bea.gov/rss/rss.xml",
    "marketwatch": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "cnbc": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
}
HEADERS = {"User-Agent": "Mozilla/5.0"}
TIMEOUT = 20
MAX_TITLE = 300


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()[:MAX_TITLE]


def published_utc(text: str | None) -> str | None:
    try:
        return parsedate_to_datetime(text.strip()).astimezone(timezone.utc).isoformat()
    except (AttributeError, TypeError, ValueError):
        return None


def parse_feed(source: str, xml: bytes) -> list[tuple]:
    """(link, source, title, published) for each RSS item."""
    rows = []
    for item in ET.fromstring(xml).iter("item"):
        link, title = clean(item.findtext("link")), clean(item.findtext("title"))
        if link and title:
            rows.append((link, source, title, published_utc(item.findtext("pubDate"))))
    return rows


def fetch_news(db) -> tuple[int, list[str]]:
    """Save new headlines. One failing feed never stops the others."""
    before = db.total_changes
    failed = []
    now = datetime.now(timezone.utc).isoformat()
    for source, url in FEEDS.items():
        try:
            response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            response.raise_for_status()
            rows = parse_feed(source, response.content)
        except (requests.RequestException, ET.ParseError):
            failed.append(source)
            continue
        db.executemany("INSERT OR IGNORE INTO news_items VALUES (?, ?, ?, ?, ?)", [(*row, now) for row in rows])
    db.commit()
    return db.total_changes - before, failed
