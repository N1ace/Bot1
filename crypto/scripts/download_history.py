"""Download / update historical futures data into data/market.sqlite.

    python scripts/download_history.py                 # BTCUSDT + ETHUSDT since 2020-01
    python scripts/download_history.py SOLUSDT --start 2020-09
    python scripts/download_history.py BTCUSDT ETHUSDT --intraday 4h   # + 4h candles (satellite)

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
    parser.add_argument("--extended", action="store_true",
                        help="also fetch spot candles from 2017-08 and pre-2020 funding (Binance REST + Bybit)")
    parser.add_argument("--intraday", choices=["1h", "4h"], help="also fetch intraday candles at this interval")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")

    store = MarketDataStore(args.db)
    added = store.update(args.symbols, start=args.start)
    print(f"\nRows added: {added}")
    if args.intraday:
        print(f"Intraday {args.intraday} rows added: {store.update_intraday(args.symbols, args.intraday, args.start)}")
    if args.extended:
        from cryptobot.backtest.history_ext import ExtendedHistory
        ext = ExtendedHistory(store, args.db)
        print(f"Extended history added: {ext.update(args.symbols)}")
        for s in args.symbols:
            for line in ext.coverage(s):
                print("  " + line)
    print("\n=== Stored history ===")
    for symbol, kind, count, first, last in store.summary():
        fmt = lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        print(f"{symbol:<9} {kind:<8} {count:>6} rows  {fmt(first)} -> {fmt(last)} UTC")
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
