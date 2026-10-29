"""The once-a-day job:  python -m cryptobot daily

Runs after the daily close (00:00 UTC), e.g. from Windows Task Scheduler:
 1. check the level allows it (level 0: collect data + report)
 2. update candles, mark candles and funding (fills in any missed days)
 3. store today's positioning snapshot (Binance keeps only 30 days)
 4. compute the market state and check data health
 5. record the run (clean day or not) -> Level 0 gate progress
 6. read the owner's Telegram answers (/approve N, /reject N, /pending)
 7. pick the tip of the day, build the daily report, send it
 8. on Mondays (or if a week was missed): weekly review saved in reports/ and sent

Anything that breaks is recorded as a failed day and sent as an alert. Nothing
here places orders or changes settings.
"""

from __future__ import annotations

import logging
import re
import traceback
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from cryptobot.advisor.report import approval_reply, daily_report, weekly_review
from cryptobot.advisor.store import AdvisorStore
from cryptobot.advisor.tips import Tip, pick_tip
from cryptobot.backtest.data import MarketDataStore
from cryptobot.config import Config
from cryptobot.levels import LEVEL0_GATE_DAYS, require_level
from cryptobot.notifications.telegram import TelegramNotifier
from cryptobot.observer import health as health_mod
from cryptobot.observer import regime as regime_mod
from cryptobot.observer.positioning import update_positioning
from cryptobot.reporting import trades as trades_report

log = logging.getLogger(__name__)

MARKET_DB = "data/market.sqlite"
REPORTS_DIR = Path("reports")


HELP = ("Commands: /today /yesterday /week /month /day 2026-10-28 (trades + stats), "
        "/stats [week|month|all], /pending, /approve N, /reject N. "
        "I only report and suggest - settings change only when you edit the config. "
        "(Commands are read once a day at the daily run.)")


def handle_commands(advisor: AdvisorStore, texts: list[str], trades_db: str = ":memory:") -> list[str]:
    """Owner commands from Telegram -> reply texts. Approval only RECORDS the answer."""
    replies = []
    for text in texts:
        cmd = text.strip()
        m = re.fullmatch(r"/(approve|reject)\s+#?(\d+)", cmd, flags=re.I)
        period = re.fullmatch(r"/(today|yesterday|week|month)", cmd, flags=re.I)
        day = re.fullmatch(r"/day\s+(\d{4}-\d{2}-\d{2})", cmd, flags=re.I)
        stats = re.fullmatch(r"/stats(?:\s+(today|yesterday|week|month|all))?", cmd, flags=re.I)
        if m:
            s = advisor.decide(int(m.group(2)), approved=m.group(1).lower() == "approve")
            replies.append(approval_reply(s) if s else f"No suggestion #{m.group(2)}.")
        elif cmd.lower() in ("/pending", "/status"):
            pending = advisor.pending()
            replies.append("No suggestions waiting." if not pending else
                           "\n".join(f"#{s['id']} {s['title']}" for s in pending))
        elif period:
            start, end, label = trades_report.period_range(period.group(1).lower())
            replies.append(trades_report.report(trades_db, start, end, label))
        elif day:
            try:
                start, end, label = trades_report.day_range(date.fromisoformat(day.group(1)))
                replies.append(trades_report.report(trades_db, start, end, label))
            except ValueError:
                replies.append(f"Not a valid date: {day.group(1)}")
        elif stats:
            start, end, label = trades_report.period_range((stats.group(1) or "month").lower())
            replies.append(trades_report.report(trades_db, start, end, label, what="stats"))
        else:
            replies.append(HELP)
    return replies


def run_daily(cfg: Config, now: pd.Timestamp | None = None, send: bool = True) -> int:
    require_level(cfg.level, "collect_data")
    now = now or pd.Timestamp.now(tz="UTC")
    day = (now.floor("D") - pd.Timedelta(days=1)).date()        # the day whose candle just closed
    advisor = AdvisorStore(cfg.sqlite_path)
    notifier = TelegramNotifier(enabled=cfg.telegram_enabled and send)
    symbols = list(cfg.pairs)
    try:
        store = MarketDataStore(MARKET_DB)
        store.update(symbols)
        update_positioning(MARKET_DB, symbols)
        regimes, market = regime_mod.compute(store, symbols, MARKET_DB)
        health = health_mod.check(store, symbols, MARKET_DB, now)
        store.close()

        advisor.record_run(day, "ok" if health.ok else "data_problems", health.problems)
        streak = advisor.clean_streak(day)

        offset, texts = notifier.owner_commands(int(advisor.get("telegram_offset", "0")))
        advisor.set("telegram_offset", str(offset))
        for reply in handle_commands(advisor, texts, cfg.sqlite_path):
            notifier.send(reply)

        require_level(cfg.level, "report")
        existing = advisor.tip_for(day)     # re-runs on the same day reuse the same tip
        if existing:
            tip = Tip(*existing)
        else:
            tip = pick_tip(regimes, market, health.problems, advisor.concepts_seen(), advisor.recent_concepts(day, 3))
            advisor.record_tip(day, tip.concept, tip.text)

        _maybe_suggest_level_up(advisor, cfg.level, streak)
        report = daily_report(day, cfg.level, regimes, market, health.problems, streak, tip, advisor.pending())
        _save(REPORTS_DIR / "daily" / f"{day:%Y-%m-%d}.txt", report)
        notifier.send(report)
        print(report)

        if _weekly_due(advisor, day):
            _weekly(advisor, notifier, cfg.level, day, streak, regimes, market)
        return 0
    except Exception as e:  # noqa: BLE001 - record and alert, then fail loudly
        log.exception("Daily run failed")
        advisor.record_run(day, "failed", [f"{type(e).__name__}: {e}"])
        notifier.send(f"Daily run {day:%Y-%m-%d} FAILED: {type(e).__name__}: {e}\n"
                      f"This day does not count toward the level gate. Details in logs/cryptobot.log.")
        log.debug(traceback.format_exc())
        return 1
    finally:
        advisor.close()


def _maybe_suggest_level_up(advisor: AdvisorStore, level: int, streak: int) -> None:
    if level == 0 and streak >= LEVEL0_GATE_DAYS:
        advisor.add_suggestion(
            "level_up", "Level 0 exit gate passed - prepare Level 1 (paper trading)?",
            f"{streak} clean daily runs in a row (gate: {LEVEL0_GATE_DAYS}). Note: Level 1 code is not built yet, "
            "and the Level 1 exit gate needs the backtest to pass strategy-risk-spec.md section 7 (it does not yet).",
            "config/config.yaml -> level: 1   (only after Level 1 is built and reviewed)")


def _weekly_due(advisor: AdvisorStore, day: date) -> bool:
    last = advisor.get("last_weekly")
    return day.weekday() == 6 or last is None or date.fromisoformat(last) <= day - timedelta(days=7)


def _weekly(advisor, notifier, level, day, streak, regimes, market) -> None:
    week = f"{day.isocalendar().year}-W{day.isocalendar().week:02d}"
    runs = advisor.runs_between(day - timedelta(days=6), day)
    text = weekly_review(week, level, runs, streak, regimes, market, advisor.pending()[:3])
    path = REPORTS_DIR / f"weekly-{week}.md"
    _save(path, text)
    advisor.set("last_weekly", day.isoformat())
    notifier.send(f"Weekly review {week} saved to {path}.\n\n" + text.replace("**", "").replace("`", ""))


def _save(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
