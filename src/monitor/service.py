"""The monitor service: runs every job on a UTC schedule and sends Telegram messages.

    hourly check    every hour at :00       numbers only
    big-move alerts continuous              WebSocket thread, AI explains the move if a key exists
    news briefing   00:05, 03:05 ... 21:05  AI summary, or raw headlines without a key
    calendar check  daily 22:00             numbers only
    weekly review   Sunday 20:00            counts of what happened
    heartbeat       every 5 minutes         a bot blocks entries if this stops for 2 hours
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from cryptobot.notifications.telegram import TelegramNotifier

from monitor import alerts as alerts_mod
from monitor import events, market, news, odds, summariser
from monitor.flags import beat, read_view, set_flag
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

    def send(self, text: str) -> None:
        self.notifier.send(text[:TELEGRAM_LIMIT])

    def alert(self, key: str, text: str, now: datetime | None = None) -> bool:
        """Log every alert; send the same kind at most once per cooldown."""
        now = now or utc_now()
        with connect(self.db) as con:
            con.execute("INSERT INTO alerts (created_utc, key, text) VALUES (?,?,?)", (iso(now), key, text))
        last = get_kv(self.db, f"alert:{key}")
        hours = self.s["alerts"]["cooldown_hours"]
        if last and now - datetime.fromisoformat(last) < timedelta(hours=hours):
            return False
        set_kv(self.db, f"alert:{key}", iso(now))
        self.send(f"MONITOR ALERT\n{text}")
        return True

    # ---- jobs -----------------------------------------------------------------------------

    def heartbeat(self) -> None:
        beat(self.db)

    def hourly(self) -> list[str]:
        cfg = self.s["stress"]
        found = market.hourly_check(self.db, cfg, self.http)
        found += events.apply_blackouts(self.db, self.s["calendar"])
        for text in found:
            self.alert(text.split(":")[0].split(" moved")[0][:40], text)
        beat(self.db)
        return found

    def calendar(self) -> list[str]:
        problems = events.refresh(self.db, self.http)
        latest = events.latest_saved(self.db)
        if latest is None or latest < utc_now():
            problems.append("No future macro dates saved: the blackout windows are not working")
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
            if risky:
                self.alert("venue_risk", "News reports a possible Binance problem. It changes nothing automatically; "
                           "please check it yourself:\n" + "\n".join(f"{i.title}\n{i.link}" for i in risky))
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
        recent = news.fresh_unused(self.db, 6, 20, now)
        note = summariser.explain_move(self.db, self.s["news"], move, recent)
        self.alert("big_move", f"{text}\n{note}" if note else text, now)

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
        threading.Thread(target=alerts_mod.run_forever, name="big-moves", daemon=True,
                         args=(self.db, self.s["stress"]["btc_1h_abs_return"],
                               self.s["alerts"]["big_move_cooldown_minutes"], self.on_big_move)).start()
        if events.latest_saved(self.db) is None:
            self.calendar()
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
        lines.append(f"[{i.importance}] {i.title}\n{i.link}")
    return "\n".join(lines + _footer(upcoming, odds_lines))


def _format_raw(items: list[news.NewsItem], upcoming: list[str], odds_lines: list[str]) -> str:
    lines = ["News headlines (no AI summary)", ""]
    for i in items[-12:][::-1]:
        lines.append(f"{i.title} ({i.source})\n{i.url}")
    return "\n".join(lines + _footer(upcoming, odds_lines))
