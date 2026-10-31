"""The monitor service: runs every job on a UTC schedule and sends Telegram messages.

    hourly check    every hour at :00       numbers only
    big-move alerts continuous              WebSocket thread, AI explains the move if a key exists
    news briefing   00:05, 03:05 ... 21:05  AI summary, or raw headlines without a key
    calendar check  daily 22:00             numbers only
    weekly review   Sunday 20:00            counts of what happened
    heartbeat       every 5 minutes         a bot blocks entries if this stops for 2 hours
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from apscheduler.events import EVENT_JOB_ERROR
from apscheduler.schedulers.blocking import BlockingScheduler
from cryptobot.notifications.telegram import TelegramNotifier

from monitor import alerts as alerts_mod
from monitor import events, market, news, odds, summariser
from monitor.flags import HOURLY_KEY, beat, mark_ok, read_view, set_flag
from monitor.store import connect, get_kv, iso, set_kv, utc_now

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 3900


class Monitor:
    def __init__(self, settings: dict, notifier=None, http=None):
        self.s = settings
        self.db = settings["database"]
        self.notifier = notifier or TelegramNotifier(enabled=True)
        self.http = http

    # ---- output ---------------------------------------------------------------------------

    def send(self, text: str) -> bool:
        return bool(self.notifier.send(text[:TELEGRAM_LIMIT]))

    def alert(self, key: str, text: str, now: datetime | None = None, cooldown_hours: float | None = None) -> bool:
        """Log every alert; send the same kind at most once per cooldown. Returns True if delivered.
        The cooldown starts only after delivery, so a Telegram outage doesn't swallow an alert."""
        now = now or utc_now()
        with connect(self.db) as con:
            con.execute("INSERT INTO alerts (created_utc, key, text) VALUES (?,?,?)", (iso(now), key, text))
        last = get_kv(self.db, f"alert:{key}")
        hours = self.s["alerts"]["cooldown_hours"] if cooldown_hours is None else cooldown_hours
        if last and now - datetime.fromisoformat(last) < timedelta(hours=hours):
            return False
        if not self.send(f"MONITOR ALERT\n{text}"):
            log.warning("Alert not delivered: %s", key)
            return False
        set_kv(self.db, f"alert:{key}", iso(now))
        return True

    def _kill_reminder(self) -> None:
        """Send the kill switch alert until Telegram takes it. It has no cooldown."""
        with connect(self.db) as con:
            row = con.execute("SELECT id, reason FROM risk_flags WHERE flag='kill_switch' AND expires_at IS NULL "
                              "ORDER BY id DESC LIMIT 1").fetchone()
        if row and get_kv(self.db, "kill_alert_id") != str(row[0]):
            if self.send(f"KILL SWITCH SET: {row[1]}\nA bot must close everything and wait for you."):
                set_kv(self.db, "kill_alert_id", str(row[0]))

    def on_job_error(self, event) -> None:
        log.error("Job %s failed: %s", event.job_id, event.exception)
        self.alert(f"job_failed:{event.job_id}", f"A monitor job failed: {type(event.exception).__name__}")

    # ---- jobs -----------------------------------------------------------------------------

    def heartbeat(self) -> None:
        beat(self.db)

    def hourly(self) -> list[str]:
        found = market.hourly_check(self.db, self.s["stress"], self.http)
        try:
            found += [(f"event:{text[:40]}", text) for text in events.apply_blackouts(self.db, self.s["calendar"])]
        except Exception as e:  # noqa: BLE001 - a failed blackout must be loud
            log.exception("Blackout check failed")
            found.append(("check_failed_blackouts", f"The event blackout check could not run: {type(e).__name__}"))
        for key, text in found:
            if key != "kill_switch":
                self.alert(key, text)
        self._kill_reminder()
        beat(self.db)
        if not any(key.startswith("check_failed") for key, _ in found):
            mark_ok(self.db, HOURLY_KEY)        # only a fully working check counts for the fail-safe
        return [text for _, text in found]

    def calendar(self) -> list[str]:
        problems = events.refresh(self.db, self.http)
        if not problems:
            set_kv(self.db, "calendar_refresh_utc", iso(utc_now()))
        problems += events.running_out(self.db, self.s["calendar"])
        for text in problems:
            self.alert(f"calendar:{text[:20]}", text)
        soon = events.upcoming(self.db, utc_now(), 48)
        if soon:
            self.send("Coming up (next 48h):\n" + "\n".join(_event_line(e) for e in soon))
        return problems

    def briefing(self) -> str | None:
        cfg = self.s["news"]
        now = utc_now()
        fetched, problems = news.fetch_all(cfg["rss_feeds"], self.http)
        news.store_new(self.db, fetched, now)
        for text in problems:
            log.warning(text)
        if len(problems) == len(cfg["rss_feeds"]):
            self.alert("feeds_down", "All news feeds failed, so there is no briefing this time.")
        items = news.fresh_unused(self.db, cfg["lookback_hours"], cfg["max_items"], now)
        if not items:
            return None
        upcoming = [_event_line(e) for e in events.upcoming(self.db, now, 72)]
        result = summariser.summarise(self.db, cfg, items, upcoming)
        lines = self._odds_lines()
        if result:
            text = _format_ai(result, upcoming, lines)
            kind, data = "ai", result.model_dump_json()
            risky = [i for i in result.items if i.venue_risk_binance]
            for i in risky:                     # one alert per story, so a small one can't hide a big one
                self.alert(f"venue_risk:{i.title[:60]}", "News reports a possible Binance problem. It changes nothing "
                           f"automatically; please check it yourself:\n{i.title}\n{i.link}")
        else:
            text = _format_raw(items, upcoming, lines)
            kind, data = "raw", None
        with connect(self.db) as con:
            con.execute("INSERT INTO news_briefings (created_utc, kind, text, data) VALUES (?,?,?,?)",
                        (iso(now), kind, text, data))
        news.mark_used(self.db, [i.url for i in items])
        self.send(text)
        return text

    def _odds_lines(self) -> list[str]:
        cfg = self.s.get("odds", {})
        return odds.top_events(cfg.get("max_events", 3), self.http) if cfg.get("enabled") else []

    def weekly(self) -> str:
        now = utc_now()
        since = iso(now - timedelta(days=7))
        with connect(self.db) as con:
            flags = con.execute("SELECT flag, COUNT(*) FROM risk_flags WHERE created_at >= ? GROUP BY flag",
                                (since,)).fetchall()
            alerts = con.execute("SELECT COUNT(*) FROM alerts WHERE created_utc >= ?", (since,)).fetchone()[0]
            briefs = con.execute("SELECT kind, COUNT(*) FROM news_briefings WHERE created_utc >= ? GROUP BY kind",
                                 (since,)).fetchall()
            tokens = con.execute("SELECT COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0) FROM llm_log "
                                 "WHERE created_utc >= ?", (since,)).fetchone()
            liq_hours = con.execute("SELECT COUNT(*) FROM liq_hours").fetchone()[0]
        lines = ["Monitor weekly review", f"Alerts: {alerts}", "Flags set: " + (", ".join(f"{f} {n}" for f, n in flags) or "none"),
                 "Briefings: " + (", ".join(f"{k} {n}" for k, n in briefs) or "none"),
                 f"Claude tokens: {tokens[0]:,} in, {tokens[1]:,} out",
                 f"Liquidation hours logged so far: {liq_hours} (trigger needs {self.s['stress']['liq_min_hours']})"]
        soon = events.upcoming(self.db, now, 7 * 24)
        lines += ["This week:"] + ([_event_line(e) for e in soon] or ["no CPI, payrolls or FOMC"])
        text = "\n".join(lines)
        self.send(text)
        return text

    def on_big_move(self, move: float) -> None:
        now = utc_now()
        cfg = self.s["stress"]
        reason = f"BTC moved {move:+.1%} within the hour"
        market.freeze_entries(self.db, reason, cfg["freeze_hours"], now)
        text = f"{reason}. No new entries for {cfg['freeze_hours']}h. Check stops are live."
        hours = self.s["alerts"]["big_move_cooldown_minutes"] / 60
        delivered = self.alert(f"big_move_{'up' if move > 0 else 'down'}", text, now, hours)   # never wait for the AI
        if delivered and summariser.available():
            threading.Thread(target=self._explain, args=(move, now), daemon=True).start()

    def _explain(self, move: float, now: datetime) -> None:
        """Follow-up with the AI's view. If it fails, the alert has already gone out."""
        note = summariser.explain_move(self.db, self.s["news"], move, news.fresh_unused(self.db, 6, 20, now))
        if note:
            self.send(f"About that BTC move:\n{note}")

    def status(self) -> str:
        view = read_view(self.db, down_after_hours=self.s["failsafe"]["monitor_down_block_hours"])
        now = utc_now()
        lines = [f"Entries allowed: {view.entries_allowed(now)}", f"New position size: x{view.vol_mult}",
                 f"Kill switch: {view.kill_switch}", f"Monitor down: {view.monitor_down}"]
        return "\n".join(lines + [f"- {r}" for r in view.reasons])

    def kill(self, reason: str) -> None:
        set_flag(self.db, "kill_switch", True, "owner", reason)
        self.send(f"KILL SWITCH SET BY OWNER: {reason}")

    # ---- run ------------------------------------------------------------------------------

    def run(self) -> None:
        beat(self.db)
        sched_cfg = self.s["schedules"]
        hour, minute = map(int, sched_cfg["calendar_check"].split(":"))
        sched = BlockingScheduler(timezone="UTC", job_defaults={"misfire_grace_time": 600, "coalesce": True})
        sched.add_job(self.hourly, "cron", minute=0)
        sched.add_job(self.briefing, "cron", hour=",".join(map(str, sched_cfg["briefing_hours"])),
                      minute=sched_cfg["briefing_minute"])
        sched.add_job(self.calendar, "cron", hour=hour, minute=minute)
        sched.add_job(self.weekly, "cron", day_of_week="sun", hour=20, minute=0)
        sched.add_job(self.heartbeat, "interval", minutes=sched_cfg["heartbeat_minutes"])
        sched.add_listener(self.on_job_error, EVENT_JOB_ERROR)
        threading.Thread(target=alerts_mod.run_forever, name="big-moves", daemon=True,
                         args=(self.db, self.s["stress"]["btc_1h_abs_return"],
                               self.s["alerts"]["big_move_cooldown_minutes"], self.on_big_move)).start()
        refreshed = get_kv(self.db, "calendar_refresh_utc")
        if refreshed is None or utc_now() - datetime.fromisoformat(refreshed) > timedelta(hours=24):
            self.calendar()                     # the PC may have been off at 22:00
        self.hourly()
        log.info("Monitor running")
        sched.start()


def _event_line(event: tuple) -> str:
    _, when, title = event
    return f"{title}: {when:%a %d %b %H:%M} UTC"


def _footer(upcoming: list[str], odds_lines: list[str]) -> list[str]:
    out = []
    if upcoming:
        out += ["", "Coming up:", *upcoming]
    if odds_lines:
        out += ["", "Market odds (context only):", *odds_lines]
    return out


def _format_ai(b: summariser.Briefing, upcoming: list[str], odds_lines: list[str]) -> str:
    order = {"high": 0, "medium": 1, "low": 2}
    lines = ["News briefing", "", b.summary, ""]
    for i in sorted(b.items, key=lambda x: order[x.importance])[:8]:
        lines.append(f"[{i.importance}] {i.title}" + (f"\n{i.link}" if i.link else ""))
    return "\n".join(lines + _footer(upcoming, odds_lines))


def _format_raw(items: list[news.NewsItem], upcoming: list[str], odds_lines: list[str]) -> str:
    lines = ["News headlines (no AI summary)", ""]
    for i in items[-12:][::-1]:
        lines.append(f"{i.title} ({i.source})\n{i.url}")
    return "\n".join(lines + _footer(upcoming, odds_lines))
