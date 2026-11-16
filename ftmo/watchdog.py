"""Warns on Telegram when the demo bot or MT5 isn't running near US hours. Task Scheduler runs it every 30 minutes.

It runs on the same PC, so it can't say that PC is off. Instead it sends "all running" once each trading day,
around 16:00 Israel time: if that message hasn't come by the US open, the PC or the bot is down.
Read-only: it never touches MT5 or an order.
"""

import subprocess
from datetime import datetime, timezone

from ftmo.bot import BOT_DIR, NEW_YORK, NO_TRADE_DAYS
from news_engine import telegram

WATCH_FROM = 9 * 60  # New York minutes: half an hour before the open
WATCH_UNTIL = 16 * 60
STALE_MINUTES = 3  # the bot saves its state every 20 seconds while connected
SENT_FILE = BOT_DIR / "watchdog_ok.txt"


def watching(ny: datetime) -> bool:
    minute = ny.hour * 60 + ny.minute
    return ny.weekday() < 5 and ny.date() not in NO_TRADE_DAYS and WATCH_FROM <= minute < WATCH_UNTIL


def mt5_running() -> bool:
    found = subprocess.run(["tasklist", "/FI", "IMAGENAME eq terminal64.exe", "/NH"], capture_output=True, text=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return "terminal64.exe" in found.stdout


def bot_minutes_quiet(now: datetime) -> float | None:
    """Minutes since the bot last saved its state. None if it has never run."""
    files = list(BOT_DIR.glob("*_demo.state.json")) or list(BOT_DIR.glob("*.state.json"))  # a dry run can't hide a dead demo bot
    if not files:
        return None
    newest = max(f.stat().st_mtime for f in files)
    return (now.timestamp() - newest) / 60


def problems(now: datetime, mt5_up: bool, quiet: float | None) -> list[str]:
    found = []
    if not mt5_up:
        found.append("MT5 isn't running")
    if quiet is None:
        found.append("the bot has never run on this PC")
    elif quiet > STALE_MINUTES:
        found.append(f"the bot hasn't updated for {quiet:.0f} minutes (stopped, or MT5 disconnected)")
    return found


def main() -> None:
    now = datetime.now(timezone.utc)
    ny = now.astimezone(NEW_YORK)
    if not watching(ny):
        return
    found = problems(now, mt5_running(), bot_minutes_quiet(now))
    if found:
        telegram.send("FTMO WARNING: " + "; ".join(found) + ". Start it with: Start-ScheduledTask -TaskName ftmo-bot")
        return
    today = ny.date().isoformat()
    if not SENT_FILE.exists() or SENT_FILE.read_text(encoding="utf-8").strip() != today:
        if telegram.send("FTMO: bot and MT5 running for today's session") == "sent":
            SENT_FILE.write_text(today, encoding="utf-8")


if __name__ == "__main__":
    main()
