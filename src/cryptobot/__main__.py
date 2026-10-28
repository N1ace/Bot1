"""Entry point:  python -m cryptobot [--config config/config.yaml] [--minutes 10]"""

from __future__ import annotations

import argparse
import logging
import sys

from dotenv import load_dotenv

from cryptobot.config import ConfigError, load_config, load_credentials
from cryptobot.logging_setup import setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(prog="cryptobot")
    parser.add_argument("--config", default="config/config.yaml", help="path to the YAML config")
    parser.add_argument("--minutes", type=float, default=10, help="futures: how long to run the streams")
    args = parser.parse_args()

    # Windows consoles default to an old code page; Binance can return non-ASCII names.
    sys.stdout.reconfigure(encoding="utf-8")

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


if __name__ == "__main__":
    sys.exit(main())
