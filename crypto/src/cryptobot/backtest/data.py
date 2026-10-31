"""Historical futures data for backtests: download, verify, store, load.

Sources (public, no API key):
- data.binance.vision  monthly zip files for USDⓈ-M futures:
    klines/<SYM>/1d/            daily candles (trade prices)
    markPriceKlines/<SYM>/1d/   daily mark-price candles (used for funding + stops)
    fundingRate/<SYM>/          every funding settlement: time, interval, rate
  Each zip has a .CHECKSUM file (SHA-256); we verify every download.
- fapi.binance.com public REST for the latest weeks that are not packaged yet.

Stored in a separate SQLite file (default data/market.sqlite) so market history
never mixes with the trade log. Re-running only fetches what is missing.

File-format quirks handled here:
- older kline files have no header row, newer ones do
- funding calc_time can be a few ms after the hour (e.g. ...800005)
- timestamps are ms; if Binance ever switches to microseconds (as spot did) we convert
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import sqlite3
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger(__name__)

VISION_BASE = "https://data.binance.vision/data/futures/um/monthly"
VISION_SPOT_BASE = "https://data.binance.vision/data/spot/monthly"
REST_BASE = "https://fapi.binance.com"
DAY_MS = 86_400_000

# dataset -> (path template, storage kind)
DATASETS = {
    "klines": ("klines/{s}/1d/{s}-1d-{ym}.zip", "trade"),
    "markPriceKlines": ("markPriceKlines/{s}/1d/{s}-1d-{ym}.zip", "mark"),
    "fundingRate": ("fundingRate/{s}/{s}-fundingRate-{ym}.zip", "funding"),
}
# Intraday candles for the satellite sleeve, stored as kind 'trade_4h' / 'mark_4h'.
INTRADAY_DATASETS = {
    "klines": ("klines/{s}/{i}/{s}-{i}-{ym}.zip", "trade_{i}"),
    "markPriceKlines": ("markPriceKlines/{s}/{i}/{s}-{i}-{ym}.zip", "mark_{i}"),
}
INTERVAL_MS = {"1h": 3_600_000, "4h": 14_400_000}
# Spot candles (from 2017-08) - only used to extend backtests to before futures existed.
SPOT_DATASET = ("klines/{s}/1d/{s}-1d-{ym}.zip", "spot")

SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    symbol TEXT NOT NULL, kind TEXT NOT NULL,      -- kind: 'trade' or 'mark'
    open_time INTEGER NOT NULL,                    -- ms since epoch, UTC
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (symbol, kind, open_time)
);
CREATE TABLE IF NOT EXISTS funding (
    symbol TEXT NOT NULL,
    time INTEGER NOT NULL,                         -- settlement time, ms since epoch, UTC
    interval_hours INTEGER,                        -- 8, 4 or 1 (NULL when unknown)
    rate REAL NOT NULL,                            -- e.g. 0.0001 = 0.01%
    PRIMARY KEY (symbol, time)
);
CREATE TABLE IF NOT EXISTS downloads (             -- which monthly files are done
    file TEXT PRIMARY KEY, sha256 TEXT, rows INTEGER, downloaded_at TEXT
);
"""


class ChecksumError(RuntimeError):
    pass


def to_ms(value) -> int:
    """Binance timestamps are ms; values above ~year 5000 in ms are microseconds."""
    v = int(value)
    return v // 1000 if v > 100_000_000_000_000 else v


def parse_kline_csv(text: str) -> list[tuple]:
    """(open_time, open, high, low, close, volume) rows; header row optional."""
    rows = []
    for rec in csv.reader(io.StringIO(text)):
        if not rec or not rec[0].strip().isdigit():
            continue  # header or blank line
        rows.append((to_ms(rec[0]), float(rec[1]), float(rec[2]), float(rec[3]), float(rec[4]), float(rec[5])))
    return rows


def parse_funding_csv(text: str) -> list[tuple]:
    """(time, interval_hours, rate) rows from calc_time,funding_interval_hours,last_funding_rate."""
    rows = []
    for rec in csv.reader(io.StringIO(text)):
        if not rec or not rec[0].strip().isdigit():
            continue
        interval = int(rec[1]) if rec[1].strip() else None
        rows.append((to_ms(rec[0]), interval, float(rec[2])))
    return rows


def verify_checksum(content: bytes, checksum_text: str) -> str:
    expected = checksum_text.split()[0].strip().lower()
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected:
        raise ChecksumError(f"SHA-256 mismatch: expected {expected}, got {actual}")
    return actual


def months(start: str, end: str) -> list[str]:
    """['2020-01', ..., end] inclusive, format YYYY-MM."""
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def last_complete_month(today: date | None = None) -> str:
    today = today or datetime.now(timezone.utc).date()
    y, m = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    return f"{y:04d}-{m:02d}"


class MarketDataStore:
    def __init__(self, path: str | Path = "data/market.sqlite", session: requests.Session | None = None):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.executescript(SCHEMA)
        self.http = session or requests.Session()

    # ---- download ----------------------------------------------------------

    def update(self, symbols: list[str], start: str = "2020-01") -> dict[str, int]:
        """Fetch everything missing for these symbols. Returns rows added per dataset."""
        added = {name: 0 for name in DATASETS} | {"rest": 0}
        end = last_complete_month()
        for symbol in symbols:
            for name in DATASETS:
                for ym in months(start, end):
                    added[name] += self._monthly_file(symbol, name, ym)
            added["rest"] += self._rest_top_up(symbol)
        return added

    def update_spot(self, symbols: list[str], start: str = "2017-08", end: str = "2019-12") -> int:
        """Spot daily candles (stored as kind 'spot'), used before futures history begins."""
        return sum(self._monthly_file(s, "spot", ym) for s in symbols for ym in months(start, end))

    def update_intraday(self, symbols: list[str], interval: str = "4h", start: str = "2020-01") -> int:
        """Intraday trade + mark candles (satellite sleeve). Funding comes from update()."""
        added = 0
        for symbol in symbols:
            for name in INTRADAY_DATASETS:
                for ym in months(start, last_complete_month()):
                    added += self._monthly_file(symbol, name, ym, interval)
            added += self._rest_top_up_bars(symbol, interval)
        return added

    def _monthly_file(self, symbol: str, dataset: str, ym: str, interval: str | None = None) -> int:
        if dataset == "spot":
            (template, kind), base, prefix = SPOT_DATASET, VISION_SPOT_BASE, "spot/"
        elif interval:
            (template, kind), base, prefix = INTRADAY_DATASETS[dataset], VISION_BASE, ""
            template, kind = template.replace("{i}", interval), kind.format(i=interval)
        else:
            (template, kind), base, prefix = DATASETS[dataset], VISION_BASE, ""
        rel = template.format(s=symbol, ym=ym)
        key = prefix + rel                     # spot and futures paths look alike: keep keys apart
        if self._db.execute("SELECT 1 FROM downloads WHERE file=?", (key,)).fetchone():
            return 0
        resp = self.http.get(f"{base}/{rel}", timeout=60)
        if resp.status_code == 404:
            log.info("Not available (symbol not listed yet?): %s", key)
            return 0
        resp.raise_for_status()
        checksum = self.http.get(f"{base}/{rel}.CHECKSUM", timeout=60)
        checksum.raise_for_status()
        sha = verify_checksum(resp.content, checksum.text)
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            text = zf.read(zf.namelist()[0]).decode("utf-8")
        if kind == "funding":
            rows = parse_funding_csv(text)
            self._db.executemany("INSERT OR REPLACE INTO funding VALUES (?,?,?,?)",
                                 [(symbol, *r) for r in rows])
        else:
            rows = parse_kline_csv(text)
            self._db.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?)",
                                 [(symbol, kind, *r) for r in rows])
        self._db.execute("INSERT INTO downloads VALUES (?,?,?,?)",
                         (key, sha, len(rows), datetime.now(timezone.utc).isoformat()))
        self._db.commit()
        log.info("Downloaded %s (%d rows, checksum OK)", key, len(rows))
        return len(rows)

    def _rest_top_up(self, symbol: str) -> int:
        """Days/funding after the last packaged month, from the public REST API.
        Only CLOSED daily bars are stored."""
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        added = 0
        for kind, path in (("trade", "/fapi/v1/klines"), ("mark", "/fapi/v1/markPriceKlines")):
            last = self._db.execute("SELECT MAX(open_time) FROM bars WHERE symbol=? AND kind=?",
                                    (symbol, kind)).fetchone()[0]
            start = (last + DAY_MS) if last else None
            if start is None or start + DAY_MS > now_ms:
                continue
            resp = self.http.get(f"{REST_BASE}{path}", timeout=30,
                                 params={"symbol": symbol, "interval": "1d", "startTime": start, "limit": 1500})
            resp.raise_for_status()
            rows = [(to_ms(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]))
                    for k in resp.json() if to_ms(k[6]) < now_ms]          # k[6] = close time
            self._db.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?)",
                                 [(symbol, kind, *r) for r in rows])
            added += len(rows)
        last = self._db.execute("SELECT MAX(time) FROM funding WHERE symbol=?", (symbol,)).fetchone()[0]
        if last:
            resp = self.http.get(f"{REST_BASE}/fapi/v1/fundingRate", timeout=30,
                                 params={"symbol": symbol, "startTime": last + 1, "limit": 1000})
            resp.raise_for_status()
            rows = [(symbol, to_ms(f["fundingTime"]), None, float(f["fundingRate"])) for f in resp.json()]
            self._db.executemany("INSERT OR REPLACE INTO funding VALUES (?,?,?,?)", rows)
            added += len(rows)
        self._db.commit()
        return added

    def _rest_top_up_bars(self, symbol: str, interval: str) -> int:
        """Closed intraday bars after the last packaged month, from the public REST API."""
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        step, added = INTERVAL_MS[interval], 0
        for kind, path in ((f"trade_{interval}", "/fapi/v1/klines"), (f"mark_{interval}", "/fapi/v1/markPriceKlines")):
            last = self._db.execute("SELECT MAX(open_time) FROM bars WHERE symbol=? AND kind=?",
                                    (symbol, kind)).fetchone()[0]
            start = last + step if last else None
            while start is not None and start + step <= now_ms:
                resp = self.http.get(f"{REST_BASE}{path}", timeout=30, params={
                    "symbol": symbol, "interval": interval, "startTime": start, "limit": 1500})
                resp.raise_for_status()
                rows = [(to_ms(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]))
                        for k in resp.json() if to_ms(k[6]) < now_ms]          # closed bars only
                if not rows:
                    break
                self._db.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?)",
                                     [(symbol, kind, *r) for r in rows])
                added += len(rows)
                start = rows[-1][0] + step
        self._db.commit()
        return added

    # ---- load --------------------------------------------------------------

    def bars(self, symbol: str, kind: str = "trade") -> pd.DataFrame:
        """Bars indexed by UTC open time (oldest first). kind: trade, mark, spot, trade_4h, mark_4h."""
        df = pd.read_sql_query("SELECT open_time, open, high, low, close, volume FROM bars"
                               " WHERE symbol=? AND kind=? ORDER BY open_time", self._db, params=(symbol, kind))
        df.index = pd.to_datetime(df.pop("open_time"), unit="ms", utc=True)
        return df

    def funding(self, symbol: str) -> pd.DataFrame:
        df = pd.read_sql_query("SELECT time, interval_hours, rate FROM funding WHERE symbol=? ORDER BY time",
                               self._db, params=(symbol,))
        df.index = pd.to_datetime(df.pop("time"), unit="ms", utc=True)
        return df

    def summary(self) -> list[tuple]:
        return self._db.execute(
            "SELECT symbol, kind, COUNT(*), MIN(open_time), MAX(open_time) FROM bars GROUP BY symbol, kind "
            "UNION ALL SELECT symbol, 'funding', COUNT(*), MIN(time), MAX(time) FROM funding GROUP BY symbol"
        ).fetchall()

    def close(self) -> None:
        self._db.close()
