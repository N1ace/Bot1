"""Events that follow fixed rules, and US market holidays.

ISM dates come from its usual pattern, not an official file: manufacturing on the first business day, services on the
third, at 10:00 New York. Monthly options expire on the third Friday. Holidays are NYSE's, read 2026-11-01.
"""

from calendar import monthrange
from datetime import date, datetime, time, timedelta

from news_engine.store import new_york_to_utc

CLOSED_DAYS = {date.fromisoformat(d) for d in (
    "2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
    "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
)}
EARLY_CLOSE_DAYS = {date.fromisoformat(d) for d in ("2026-11-27", "2026-12-24", "2027-11-26")}

ISM_TIME = time(10, 0)
OPEX_TIME = time(16, 0)
ISM_TIER, OPEX_TIER = 2, 3
MONTHS_AHEAD = 13


def is_business_day(day: date) -> bool:
    return day.weekday() < 5 and day not in CLOSED_DAYS


def nth_business_day(year: int, month: int, n: int) -> date:
    day = date(year, month, 1)
    while True:
        if is_business_day(day):
            n -= 1
            if n == 0:
                return day
        day += timedelta(days=1)


def third_friday(year: int, month: int) -> date:
    """The last trading day of that expiry week, which moves to Thursday if Friday is closed."""
    first = date(year, month, 1)
    day = first + timedelta(days=(4 - first.weekday()) % 7 + 14)
    return day if is_business_day(day) else day - timedelta(days=1)


def rule_events(today: date) -> list[tuple]:
    rows = []
    year, month = today.year, today.month
    for _ in range(MONTHS_AHEAD):
        for kind, day, at, tier in (("ISM_MFG", nth_business_day(year, month, 1), ISM_TIME, ISM_TIER),
                                    ("ISM_SERV", nth_business_day(year, month, 3), ISM_TIME, ISM_TIER),
                                    ("OPEX", third_friday(year, month), OPEX_TIME, OPEX_TIER)):
            if day >= today:
                rows.append((f"{kind}-{day}", kind, tier, new_york_to_utc(day.isoformat(), at).isoformat(), "rule"))
        month = month % 12 + 1
        year += month == 1
    return rows


def days_in_month(year: int, month: int) -> int:
    return monthrange(year, month)[1]


def no_trade_day(day: date) -> bool:
    return day in CLOSED_DAYS or day in EARLY_CLOSE_DAYS


def next_session_gap(moment: datetime) -> bool:
    """True if the market won't reopen tomorrow: a Friday, or the day before a holiday."""
    day = moment.date()
    following = day + timedelta(days=1)
    return day.weekday() == 4 or following in CLOSED_DAYS
