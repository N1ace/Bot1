"""Entry point.

    python -m cryptobot daily            # the once-a-day job (level 0: data + market state + report)
    python -m cryptobot daily --no-send  # same, but don't send to Telegram
    python -m cryptobot                  # futures demo: startup routine + streams for --minutes

    python -m cryptobot trades --period today|yesterday|week|month|all
    python -m cryptobot trades --day 2026-10-28
    python -m cryptobot trades --from 2026-10-01 --to 2026-10-31
    python -m cryptobot stats  --period month          # same options; profit/loss, win rate, fees, funding
"""

from __future__ import annotations

import argparse
import logging
import sys

from dotenv import load_dotenv

from cryptobot.config import ConfigError, load_config, load_credentials
from cryptobot.logging_setup import setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(prog="cryptobot")
    parser.add_argument("command", nargs="?", default="run", choices=["run", "daily", "trades", "stats"])
    parser.add_argument("--config", default="config/config.yaml", help="path to the YAML config")
    parser.add_argument("--minutes", type=float, default=10, help="futures: how long to run the streams")
    parser.add_argument("--no-send", action="store_true", help="daily: don't send to Telegram")
    parser.add_argument("--period", default="week", choices=["today", "yesterday", "week", "month", "all"])
    parser.add_argument("--day", help="trades/stats for one UTC day, YYYY-MM-DD")
    parser.add_argument("--from", dest="date_from", help="first UTC day, YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", help="last UTC day, YYYY-MM-DD (default: today)")
    args = parser.parse_args()

    # Windows consoles default to an old code page; Binance can return non-ASCII names.
    sys.stdout.reconfigure(encoding="utf-8")

    if args.command in ("trades", "stats"):
        return _history(args)

    load_dotenv()  # reads .env from the current folder into environment variables
    try:
        cfg = load_config(args.config)
        creds = load_credentials(cfg.account, cfg.environment)
    except (ConfigError, FileNotFoundError, ValueError) as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2

    setup_logging(cfg.log_level, cfg.log_dir, secrets=creds.secret_values if creds else [])
    log = logging.getLogger("cryptobot")
    log.info("Starting cryptobot: mode=%s environment=%s account=%s", cfg.mode, cfg.environment, cfg.account)

    try:
        if args.command == "daily":
            from cryptobot.observer.daily import run_daily
            return run_daily(cfg, send=not args.no_send)
        if cfg.mode == "futures":
            from cryptobot.modes import futures
            return futures.run(cfg, args.minutes)
        if cfg.mode == "spot":
            from cryptobot.modes import spot
            spot.run(cfg)
            return 0
    except KeyboardInterrupt:
        log.info("Stopped by user (Ctrl+C)")
        return 130
    log.error("Mode %r is not implemented yet", cfg.mode)
    return 1


def _history(args) -> int:
    """Read-only: trades / statistics from the trade log. Needs no keys and no level."""
    from datetime import date

    from cryptobot.reporting import trades as tr

    try:
        cfg = load_config(args.config)
        if args.day:
            start, end, label = tr.day_range(date.fromisoformat(args.day))
        elif args.date_from:
            last = date.fromisoformat(args.date_to) if args.date_to else date.today()
            start, end, label = tr.custom_range(date.fromisoformat(args.date_from), last)
        else:
            start, end, label = tr.period_range(args.period)
    except (ConfigError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    print(tr.report(cfg.sqlite_path, start, end, label, what=args.command))
    return 0


if __name__ == "__main__":
    sys.exit(main())
