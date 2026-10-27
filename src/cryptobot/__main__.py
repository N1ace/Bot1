"""Entry point:  python -m cryptobot [--config config/config.yaml]"""

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
    args = parser.parse_args()

    # Windows consoles default to an old code page; Binance can return non-ASCII names.
    sys.stdout.reconfigure(encoding="utf-8")

    load_dotenv()  # reads .env from the current folder into environment variables
    try:
        cfg = load_config(args.config)
    except (ConfigError, FileNotFoundError) as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2

    creds = load_credentials(cfg.account, cfg.environment)
    setup_logging(cfg.log_level, cfg.log_dir, secrets=[creds.api_key, creds.api_secret] if creds else [])
    log = logging.getLogger("cryptobot")
    log.info("Starting cryptobot: mode=%s environment=%s account=%s", cfg.mode, cfg.environment, cfg.account)

    if cfg.mode == "spot":
        from cryptobot.modes import spot
        spot.run(cfg)
    else:
        log.error("Mode %r is not implemented yet", cfg.mode)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
