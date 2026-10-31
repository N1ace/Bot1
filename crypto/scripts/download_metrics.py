"""Archived daily positioning history (open interest, long/short ratios) into data/market.sqlite.

    python scripts/download_metrics.py

The live API only keeps 30 days, but data.binance.vision keeps daily files of 5-minute
snapshots from 2020-09. We keep each day's LAST snapshot. Safe to re-run.
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor

import requests

from cryptobot.backtest.data import verify_checksum

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"]
ARCHIVE = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
VISION = "https://data.binance.vision/data/futures/um/daily/metrics"
SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics_daily (
    symbol TEXT NOT NULL, day TEXT NOT NULL,        -- UTC date of the file
    open_interest_value REAL,                       -- USDT value of open positions at the day's end
    top_trader_ratio REAL, account_ratio REAL, taker_ratio REAL,
    PRIMARY KEY (symbol, day)
);
"""


def days_in_archive(symbol: str) -> list[str]:
    out, marker = [], ""
    while True:
        text = requests.get(ARCHIVE, params={"prefix": f"data/futures/um/daily/metrics/{symbol}/", "marker": marker},
                            timeout=60).text
        keys = re.findall(r"<Key>([^<]+\.zip)</Key>", text)
        out += [re.search(r"(\d{4}-\d{2}-\d{2})\.zip$", k).group(1) for k in keys]
        if "<IsTruncated>true" not in text or not keys:
            return out
        marker = re.findall(r"<Key>([^<]+)</Key>", text)[-1]


def fetch(job: tuple[str, str]) -> tuple | None:
    symbol, day = job
    url = f"{VISION}/{symbol}/{symbol}-metrics-{day}.zip"
    for attempt in range(4):
        try:
            resp = requests.get(url, timeout=60)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            check = requests.get(url + ".CHECKSUM", timeout=60)
            check.raise_for_status()
            verify_checksum(resp.content, check.text)
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                rows = list(csv.DictReader(io.StringIO(zf.read(zf.namelist()[0]).decode("utf-8"))))
            if not rows:
                return None
            last = rows[-1]

            def num(key):
                return float(last[key]) if last.get(key) else None
            return (symbol, day, num("sum_open_interest_value"), num("sum_toptrader_long_short_ratio"),
                    num("count_long_short_ratio"), num("sum_taker_long_short_vol_ratio"))
        except requests.RequestException:
            if attempt == 3:
                raise
    return None


def main() -> int:
    con = sqlite3.connect("data/market.sqlite", timeout=60)
    con.executescript(SCHEMA)
    for symbol in SYMBOLS:
        have = {r[0] for r in con.execute("SELECT day FROM metrics_daily WHERE symbol=?", (symbol,))}
        jobs = [(symbol, d) for d in days_in_archive(symbol) if d not in have]
        with ThreadPoolExecutor(8) as pool:
            rows = [r for r in pool.map(fetch, jobs) if r]
        con.executemany("INSERT OR REPLACE INTO metrics_daily VALUES (?,?,?,?,?,?)", rows)
        con.commit()
        print(symbol, "added", len(rows), flush=True)
    print(con.execute("SELECT symbol, COUNT(*), MIN(day), MAX(day), COUNT(open_interest_value) FROM metrics_daily "
                      "GROUP BY symbol").fetchall())
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
