"""Daily snapshots of Binance's market-positioning data.

These public endpoints only keep the LAST 30 DAYS, so we store them ourselves
every day from day one (strategy-risk-spec.md section 6):
  openInterestHist            how much is invested in open positions (coins and USDT)
  topLongShortPositionRatio   top traders: long vs short position size
  globalLongShortAccountRatio all accounts: share of accounts long vs short
  takerlongshortRatio         aggressive buyers vs sellers (taker volume)
Used as context / risk filters only, never as trade signals.
"""

from __future__ import annotations

import logging
import sqlite3

import requests

log = logging.getLogger(__name__)

BASE = "https://fapi.binance.com/futures/data"

# endpoint -> the field we keep as the main value
METRICS = {
    "openInterestHist": "sumOpenInterestValue",          # USDT value of open interest
    "topLongShortPositionRatio": "longShortRatio",
    "globalLongShortAccountRatio": "longShortRatio",
    "takerlongshortRatio": "buySellRatio",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS positioning (
    symbol TEXT NOT NULL,
    metric TEXT NOT NULL,
    time INTEGER NOT NULL,          -- ms since epoch, UTC (start of the daily period)
    value REAL NOT NULL,
    PRIMARY KEY (symbol, metric, time)
);
"""


def update_positioning(db_path: str, symbols: list[str], session: requests.Session | None = None) -> int:
    """Fetch the last 30 daily points of each metric and store new ones. Returns rows added."""
    http = session or requests.Session()
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    before = con.execute("SELECT COUNT(*) FROM positioning").fetchone()[0]
    for symbol in symbols:
        for metric, field in METRICS.items():
            resp = http.get(f"{BASE}/{metric}", params={"symbol": symbol, "period": "1d", "limit": 30}, timeout=30)
            resp.raise_for_status()
            rows = [(symbol, metric, int(r["timestamp"]), float(r[field])) for r in resp.json()]
            con.executemany("INSERT OR REPLACE INTO positioning VALUES (?,?,?,?)", rows)
    con.commit()
    added = con.execute("SELECT COUNT(*) FROM positioning").fetchone()[0] - before
    con.close()
    log.info("Positioning data: %d new rows", added)
    return added


def load_positioning(db_path: str, symbol: str, metric: str):
    import pandas as pd
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    df = pd.read_sql_query("SELECT time, value FROM positioning WHERE symbol=? AND metric=? ORDER BY time",
                           con, params=(symbol, metric))
    con.close()
    df.index = pd.to_datetime(df.pop("time"), unit="ms", utc=True)
    return df["value"]
