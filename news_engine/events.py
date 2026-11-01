"""Past event dates from official sources.

FOMC comes from the Fed's calendar page. CPI and NFP dates come from FRED, because BLS refuses requests from this PC.
The key is read from the root .env and never printed.
"""

import html
import os
import re
from datetime import time
from pathlib import Path

import requests

from news_engine.store import new_york_to_utc

FOMC_PAGE = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FRED_DATES = "https://api.stlouisfed.org/fred/release/dates"
FRED_RELEASE = "https://api.stlouisfed.org/fred/release"
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
HEADERS = {"User-Agent": "Mozilla/5.0"}
TIMEOUT = 30

MONTHS = ("January February March April May June July August September October November December").split()
MEETING = re.compile(rf"({'|'.join(MONTHS)})(?:/({'|'.join(MONTHS)}))? (\d{{1,2}})-(\d{{1,2}})")
STATEMENT = time(14, 0)
DATA_RELEASE = time(8, 30)

# FRED release ID, the words its name must contain, and tier. The name check catches a wrong ID
FRED_RELEASES = {"CPI": (10, "consumer price index", 1), "NFP": (50, "employment situation", 1)}


def parse_fomc(page: str) -> list[tuple[str, str]]:
    """(type-free id date, statement day) for each two-day meeting. The statement comes on the second day."""
    text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page)))
    starts = [(m.start(), int(m.group(1))) for m in re.finditer(r"(\d{4}) FOMC Meetings", text)]
    found = []
    for (start, year), (end, _) in zip(starts, starts[1:] + [(len(text), 0)]):
        for first, second, _, last in MEETING.findall(text[start:end]):
            found.append(f"{year}-{MONTHS.index(second or first) + 1:02d}-{int(last):02d}")
    return sorted(set(found))


def fomc_events() -> list[tuple]:
    response = requests.get(FOMC_PAGE, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return [(f"FOMC-{day}", "FOMC", 1, new_york_to_utc(day, STATEMENT).isoformat(), "federalreserve.gov")
            for day in parse_fomc(response.text)]


def fred_key() -> str | None:
    if os.environ.get("FRED_API_KEY"):
        return os.environ["FRED_API_KEY"]
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("FRED_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None


def fred_get(url: str, params: dict) -> dict:
    """Errors never show the address, because it holds the key."""
    try:
        response = requests.get(url, params=params, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise RuntimeError(f"FRED request failed: {type(e).__name__}") from None
    if not response.ok:
        raise RuntimeError(f"FRED refused the request: status {response.status_code}")
    return response.json()


def fred_events(key: str, start: str = "2021-10-01") -> list[tuple]:
    rows = []
    for name, (release_id, expected, tier) in FRED_RELEASES.items():
        params = {"release_id": release_id, "api_key": key, "file_type": "json"}
        title = fred_get(FRED_RELEASE, params)["releases"][0]["name"]
        if expected not in title.lower():
            raise RuntimeError(f"FRED release {release_id} is {title!r}, expected {name}")
        dates = fred_get(FRED_DATES, {
            **params, "realtime_start": start, "include_release_dates_with_no_data": "false", "limit": 10000})
        for row in dates["release_dates"]:
            day = row["date"]
            rows.append((f"{name}-{day}", name, tier, new_york_to_utc(day, DATA_RELEASE).isoformat(), "fred.stlouisfed.org"))
    return rows


def save_events(db, rows: list[tuple]) -> None:
    db.executemany("INSERT OR REPLACE INTO events VALUES (?, ?, ?, ?, ?)", rows)
    db.commit()
