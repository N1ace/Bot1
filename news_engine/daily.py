"""Daily job: save estimates before upcoming events, then score the ones that have happened.

An estimate is only saved while its event is still in the future, so the score is a real forward test. Reads prices from
MT5 and never trades. If MT5 isn't running, the job still scores and predicts from the prices it has.

    python -m news_engine.daily
"""

import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from news_engine import ai, analog, briefing, events, news, positioning, reactions, regime, schedule, store, telegram

HORIZON_HOURS = 48
PRICE_TIMEOUT = 180
LOG_PATH = store.DB_PATH.with_name("news_engine.log")
SENT_PATH = store.DB_PATH.with_name("news_sent.txt")


def update_prices() -> bool:
    """A separate process adds MT5's latest bars, so nothing in the news engine touches MT5."""
    try:
        done = subprocess.run([sys.executable, "-m", "ftmo.mt5_data", "--latest"], timeout=PRICE_TIMEOUT, capture_output=True,
                              cwd=Path(__file__).resolve().parent.parent)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def past_moves(db, kind: str, symbol: str, window: str, before: str) -> pd.DataFrame:
    query = """SELECT e.scheduled_utc AS scheduled, ABS(r.return_pct) AS size, r.pre_vol_pct
               FROM reactions r JOIN events e ON e.id = r.event_id
               WHERE e.type = ? AND r.symbol = ? AND r.window = ? AND e.scheduled_utc < ?
                     AND r.pre_vol_pct IS NOT NULL ORDER BY e.scheduled_utc"""
    past = pd.read_sql_query(query, db, params=(kind, symbol, window, before))
    past["when"] = pd.to_datetime(past["scheduled"], utc=True, format="ISO8601")
    return past


def predict(db, now: datetime, volatility) -> int:
    """Save one estimate per market and window for events within the next two days."""
    upcoming = db.execute("SELECT id, type, scheduled_utc FROM events WHERE scheduled_utc > ? AND scheduled_utc <= ?",
                          (now.isoformat(), (now + timedelta(hours=HORIZON_HOURS)).isoformat())).fetchall()
    saved = 0
    for event_id, kind, scheduled in upcoming:
        for symbol in reactions.SYMBOLS:
            vol = volatility(symbol)
            if vol is None:
                continue
            for window in reactions.WINDOWS:
                past = past_moves(db, kind, symbol, window, scheduled)
                if len(past) < analog.MIN_PAST_EVENTS:
                    continue
                estimate = analog.analog_estimate(past, pd.Timestamp(scheduled), vol)
                baseline = analog.baseline_estimate(past)
                saved += db.execute(
                    "INSERT OR IGNORE INTO predictions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)",
                    (event_id, symbol, window, *map(float, estimate), *map(float, baseline), len(past), vol,
                     now.isoformat())).rowcount
    db.commit()
    return saved


def score(db) -> int:
    """Fill in the outcome for each estimate whose event now has a measured reaction."""
    rows = db.execute("""
        SELECT p.event_id, p.symbol, p.window, p.q25, p.q50, p.q75, p.b25, p.b50, p.b75, ABS(r.return_pct)
        FROM predictions p JOIN reactions r USING (event_id, symbol, window) WHERE p.outcome IS NULL""").fetchall()
    for event_id, symbol, window, *numbers, outcome in rows:
        loss = analog.pinball(outcome, np.array(numbers[:3]))
        base_loss = analog.pinball(outcome, np.array(numbers[3:6]))
        db.execute("UPDATE predictions SET outcome = ?, loss = ?, base_loss = ? WHERE event_id = ? AND symbol = ? AND window = ?",
                   (outcome, loss, base_loss, event_id, symbol, window))
    db.commit()
    return len(rows)


def scorecard(db) -> str:
    events_done, loss, base = db.execute("""SELECT COUNT(DISTINCT event_id), AVG(loss), AVG(base_loss)
                                            FROM predictions WHERE outcome IS NOT NULL""").fetchone()
    if not events_done:
        return "No live scores yet."
    return f"Live scored events {events_done}, gate 40. Average loss {loss:.3f}, baseline {base:.3f}."


def send_once(text: str, now: datetime) -> str:
    """One briefing a day, however many times the PC restarts."""
    today = f"{now:%Y-%m-%d}"
    if SENT_PATH.exists() and SENT_PATH.read_text(encoding="utf-8").strip() == today:
        return "already sent today"
    status = telegram.send(text)
    if status == "sent":
        SENT_PATH.write_text(today, encoding="utf-8")
    return status


def main() -> None:
    db = store.connect()
    now = datetime.now(timezone.utc)
    prices = update_prices()
    rows = events.fomc_events() + events.ecb_events() + schedule.rule_events(now.date())
    key = events.fred_key()
    if key:
        rows += events.fred_events(key)
    events.save_events(db, rows)
    db.execute("INSERT OR REPLACE INTO health VALUES ('calendar', ?)", (now.isoformat(),))
    db.commit()
    reactions.record_all(db)
    fresh, failed = news.fetch_news(db)
    ai_status = ai.review(db, now)
    conditions = regime.update(db, now)
    crowd = positioning.update(db, now)
    bars = {s: reactions.load_bars(s) for s in reactions.SYMBOLS}
    saved = predict(db, now, lambda s: reactions.pre_volatility(bars[s], pd.Timestamp(now)))
    briefing.write_blackouts(db, now)
    line = (f"{now:%Y-%m-%d %H:%M} UTC, MT5 prices {'updated' if prices else 'NOT updated'}, {saved} estimates saved, "
            f"{score(db)} scored, {fresh} headlines, feeds failed {failed or 'none'}, AI: {ai_status}, regime: {conditions}, positioning: {crowd}. {scorecard(db)}")
    text = briefing.make_briefing(db, now)
    briefing.BRIEFING_PATH.write_text(text, encoding="utf-8")
    line += f" Telegram: {send_once(text, now)}."
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


if __name__ == "__main__":
    main()
