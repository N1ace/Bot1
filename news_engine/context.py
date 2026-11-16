"""The read-only contract with the trading bot: what each tactic may do right now.

Stage 1 is calendar only. Any doubt fails closed: an error or an old calendar blocks new entries. It can only block or
shrink, never add size. The trading bot doesn't read this yet.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from news_engine import profiles, schedule, store

SYMBOLS = ("US100", "US500")
EVENT_TYPES = {
    "US100": ("CPI", "NFP", "FOMC", "FOMC_PRESS", "PPI", "PCE", "RETAIL", "GDP", "ISM_MFG", "ISM_SERV", "ECB", "OPEX"),
    "US500": ("CPI", "NFP", "FOMC", "FOMC_PRESS", "PPI", "PCE", "RETAIL", "GDP", "ISM_MFG", "ISM_SERV", "ECB", "OPEX"),
}
LOOK_AHEAD_HOURS, LOOK_BACK_HOURS = 24, 2
RELEASE_MINUTES = 3  # FTMO's 2 minutes plus 1 of our own
DEFAULT_PRE, DEFAULT_POST = {1: 15, 2: 10, 3: 0}, {1: 30, 2: 15, 3: 0}
CALENDAR_LIMIT_HOURS = 26  # the job runs daily, so this allows for a late start
NEXT_EVENT_DAYS = {"CPI": 45, "NFP": 45, "FOMC": 70}  # no saved date this far ahead means the calendar is broken

FRIDAY_CLOSE_NY = 16 * 60 + 50  # 23:50 on the MT5 server
GAP_BLOCK_MINUTES = 120  # FTMO: nothing opens within 2 hours of a close of 2 hours or more


@dataclass(frozen=True)
class EventWindow:
    event_id: str
    name: str
    tier: int
    time_utc: datetime
    phase: str  # pre, release, post or none
    ftmo_restricted: bool


@dataclass(frozen=True)
class TacticPermission:
    tactic: str
    allowed: bool
    size_mult: float  # 0 to 1, never above 1
    mode: str
    stop_mult: float  # 1 for now: stress rules come in a later stage
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class MarketContext:
    symbol: str
    as_of_utc: datetime
    situation: str
    events: tuple[EventWindow, ...]
    regime: str
    permissions: dict[str, TacticPermission]
    manage_positions: tuple[str, ...]
    stale: bool
    reasons: tuple[str, ...]


def phase_of(moment: datetime, now: datetime, pre: int, post: int) -> str:
    minutes = (now - moment).total_seconds() / 60
    if abs(minutes) <= RELEASE_MINUTES:
        return "release"
    if -pre <= minutes < 0:
        return "pre"
    if 0 < minutes <= post:
        return "post"
    return "none"


def gap_block(ny: datetime) -> bool:
    """FTMO's rule on opening near a long close: the last two hours before a weekend or a closed day."""
    return schedule.next_session_gap(ny) and ny.hour * 60 + ny.minute >= FRIDAY_CLOSE_NY - GAP_BLOCK_MINUTES


def calendar_problems(db, now: datetime) -> list[str]:
    row = db.execute("SELECT at_utc FROM health WHERE name = 'calendar'").fetchone()
    if row is None:
        return ["calendar never refreshed"]
    problems = []
    if now - datetime.fromisoformat(row[0]) > timedelta(hours=CALENDAR_LIMIT_HOURS):
        problems.append("calendar is old")
    for kind, days in NEXT_EVENT_DAYS.items():
        found = db.execute("SELECT 1 FROM events WHERE type = ? AND scheduled_utc > ? AND scheduled_utc < ? LIMIT 1",
                           (kind, now.isoformat(), (now + timedelta(days=days)).isoformat())).fetchone()
        if found is None:
            problems.append(f"no upcoming {kind} date")
    return problems


def nearby_events(db, symbol: str, now: datetime) -> list[tuple]:
    start = (now - timedelta(hours=LOOK_BACK_HOURS)).isoformat()
    end = (now + timedelta(hours=LOOK_AHEAD_HOURS)).isoformat()
    marks = ",".join("?" * len(EVENT_TYPES[symbol]))
    rows = db.execute(f"SELECT id, type, tier, scheduled_utc FROM events WHERE type IN ({marks}) "
                      "AND scheduled_utc >= ? AND scheduled_utc <= ? ORDER BY scheduled_utc",
                      (*EVENT_TYPES[symbol], start, end)).fetchall()
    return [(event_id, kind, tier, datetime.fromisoformat(scheduled)) for event_id, kind, tier, scheduled in rows]


def permission(name: str, profile: dict, symbol: str, situation: str, rows: list[tuple], now: datetime, market_shut: str | None,
               stale: bool, why: tuple[str, ...]) -> TacticPermission:
    allowed, size, reasons = True, 1.0, list(why)
    if stale or symbol not in profile["symbols"]:
        allowed = False
    if market_shut:
        allowed = False
        reasons.append(market_shut)
    if situation not in profile["situations_allowed"] and situation != "pre_event":
        allowed = False
        reasons.append(f"situation {situation}")
    for _, kind, tier, moment in rows:
        phase = phase_of(moment, now, profile["pre_minutes"][tier], profile["post_minutes"][tier])
        action = profile["event_sensitivity"][tier]
        if phase == "none":
            continue
        if phase == "release" or action == "block":
            allowed = False
            reasons.append(f"{kind} {phase}")
        elif action == "half":
            size = min(size, 0.5)
            reasons.append(f"{kind} {phase}: half size")
    return TacticPermission(name, allowed, size if allowed else 0.0, situation, 1.0, tuple(reasons))


def build(db, symbol: str, now: datetime) -> MarketContext:
    if symbol not in SYMBOLS:
        raise ValueError(f"unknown symbol {symbol}")
    problems = calendar_problems(db, now)
    rows = nearby_events(db, symbol, now)
    windows, instructions = [], []
    phases = set()
    for event_id, kind, tier, moment in rows:
        phase = phase_of(moment, now, DEFAULT_PRE[tier], DEFAULT_POST[tier])
        restricted = tier <= 2 and abs((now - moment).total_seconds()) <= profiles.FTMO_NEWS_MINUTES * 60
        windows.append(EventWindow(event_id, kind, tier, moment, phase, restricted))
        if tier <= 2:
            phases.add(phase)
        if phase == "pre":
            instructions.append("no_new_adds")
        if restricted:
            instructions.append(f"no_open_or_close_until:{moment + timedelta(minutes=profiles.FTMO_NEWS_MINUTES):%Y-%m-%dT%H:%MZ}")

    situation = "release" if "release" in phases else "pre_event" if "pre" in phases else \
        "post_event" if "post" in phases else "normal"
    ny = now.astimezone(store.NEW_YORK).replace(tzinfo=None)
    market_shut = None
    if ny.weekday() >= 5 or schedule.no_trade_day(ny.date()):
        market_shut = "market closed or short day"
    elif gap_block(ny):
        market_shut = "FTMO long-close rule"

    regime = db.execute("SELECT label FROM regime ORDER BY day DESC LIMIT 1").fetchone()
    why = tuple(problems)
    permissions = {name: permission(name, profile, symbol, situation, rows, now, market_shut, bool(problems), why)
                   for name, profile in profiles.load_active().items()}
    return MarketContext(symbol, now, situation, tuple(windows), regime[0] if regime else "unknown", permissions,
                         tuple(dict.fromkeys(instructions)), bool(problems), why)


def get_context(symbol: str, now: datetime | None = None, path=store.DB_PATH) -> MarketContext:
    now = now or datetime.now(timezone.utc)
    db = store.read_only(path)
    try:
        return build(db, symbol, now)
    finally:
        db.close()


def safe_get_context(symbol: str, now: datetime | None = None, path=store.DB_PATH) -> MarketContext:
    """Never raises. Any failure gives a stale context with every tactic blocked."""
    now = now or datetime.now(timezone.utc)
    try:
        return get_context(symbol, now, path)
    except Exception as e:  # noqa: BLE001 - the whole point is that nothing here can reach the trading bot as a crash
        reason = (f"context failed: {type(e).__name__}",)
        blocked = {name: TacticPermission(name, False, 0.0, "unknown", 1.0, reason) for name in profiles.ACTIVE}
        return MarketContext(symbol, now, "unknown", (), "unknown", blocked, (), True, reason)
