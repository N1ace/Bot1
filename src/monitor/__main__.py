"""Run the monitor.

    python -m monitor                  # run all day (the scheduled task uses this)
    python -m monitor once             # run each job one time, then stop (a test)
    python -m monitor status           # what would a bot be allowed to do right now?
    python -m monitor kill "reason"    # owner: set the kill switch
    python -m monitor clear-kill "why" # owner: end the kill switch
"""

from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

from monitor.flags import clear_kill_switch
from monitor.service import Monitor
from monitor.store import load_settings


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m monitor")
    parser.add_argument("command", nargs="?", default="run", choices=["run", "once", "status", "kill", "clear-kill"])
    parser.add_argument("reason", nargs="?", default="")
    args = parser.parse_args()

    load_dotenv(".env")
    Path("logs").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
                        handlers=[RotatingFileHandler("logs/monitor.log", maxBytes=2_000_000, backupCount=3,
                                                      encoding="utf-8")])
    logging.getLogger("httpx2").setLevel(logging.WARNING)   # request lines carry the Telegram token in the URL
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    settings = load_settings()
    monitor = Monitor(settings)
    if args.command == "run":
        monitor.run()
    elif args.command == "once":
        print("Calendar problems:", monitor.calendar() or "none")
        print("Market findings:", monitor.hourly() or "none")
        print("Briefing:", monitor.briefing() or "no new headlines")
        print(monitor.status())
    elif args.command == "status":
        print(monitor.status())
    elif args.command == "kill":
        if not args.reason:
            parser.error('give a reason: python -m monitor kill "reason"')
        monitor.kill(args.reason)
        print("Kill switch set.")
    elif args.command == "clear-kill":
        if not args.reason:
            parser.error('give a reason: python -m monitor clear-kill "reason"')
        print("Cleared:", clear_kill_switch(settings["database"], args.reason))
    return 0


if __name__ == "__main__":
    sys.exit(main())
