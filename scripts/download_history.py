"""Download / update historical futures data into data/market.sqlite.

    python scripts/download_history.py                 # BTCUSDT + ETHUSDT since 2020-01
    python scripts/download_history.py SOLUSDT --start 2020-09

Public data only (data.binance.vision + public REST), no API key. Safe to re-run:
only missing months are fetched. Every zip is checked against its SHA-256 file.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

from cryptobot.backtest.data import MarketDataStore


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("symbols", nargs="*", default=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--start", default="2020-01", help="first month, YYYY-MM")
    parser.add_argument("--db", default="data/market.sqlite")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")

    store = MarketDataStore(args.db)
    added = store.update(args.symbols, start=args.start)
    print(f"\nRows added: {added}")
    print("\n=== Stored history ===")
    for symbol, kind, count, first, last in store.summary():
        fmt = lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        print(f"{symbol:<9} {kind:<8} {count:>6} rows  {fmt(first)} -> {fmt(last)} UTC")
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
