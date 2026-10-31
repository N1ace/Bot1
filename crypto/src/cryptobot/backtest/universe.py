"""Full history of USDT perpetuals INCLUDING delisted coins, for cross-sectional tests.

A test that only uses coins alive today has survivorship bias: the coins that collapsed
are missing, which flatters any strategy that buys strength. So the symbol list comes
from the data.binance.vision archive (it keeps dead coins), and each week's tradable
universe is rebuilt from what was known that week (see strategies/xs_momentum.py).

Sources (public, no key): archive listing, /fapi/v1/klines, /fapi/v1/fundingRate. Coins fully
removed from the exchange make the API answer 400, so those come from the archive's zip files.
Stored in its own file, data/universe.sqlite. Quote volume (USDT traded) is kept because
the universe is ranked by it.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import sqlite3
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

from cryptobot.backtest.data import VISION_BASE, parse_funding_csv, to_ms, verify_checksum

log = logging.getLogger(__name__)

ARCHIVE = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
REST = "https://fapi.binance.com"
DAY_MS = 86_400_000
# Stablecoins have no real price moves to rank; they'd only add noise to the universe.
STABLES = {"USDCUSDT", "BUSDUSDT", "TUSDUSDT", "FDUSDUSDT", "USDPUSDT", "DAIUSDT", "USDEUSDT", "USD1USDT",
           "RLUSDUSDT", "EURUSDT", "XUSDUSDT", "BFUSDUSDT", "USDSUSDT", "PYUSDUSDT"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS ubars (
    symbol TEXT NOT NULL, open_time INTEGER NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL, quote_volume REAL,
    PRIMARY KEY (symbol, open_time)
);
CREATE TABLE IF NOT EXISTS ufunding (
    symbol TEXT NOT NULL, time INTEGER NOT NULL, rate REAL NOT NULL, PRIMARY KEY (symbol, time)
);
CREATE TABLE IF NOT EXISTS usymbols (
    symbol TEXT PRIMARY KEY, status TEXT, underlying_type TEXT, bars_done INTEGER DEFAULT 0, funding_done INTEGER DEFAULT 0
);
"""


def archive_symbols(http: requests.Session) -> list[str]:
    """Every USDT-M symbol that ever had monthly klines, dead or alive."""
    out, marker = [], ""
    while True:
        resp = http.get(ARCHIVE, params={"delimiter": "/", "prefix": "data/futures/um/monthly/klines/",
                                         "marker": marker}, timeout=60)
        resp.raise_for_status()        # a failed page must stop us: it would silently drop dead coins
        text = resp.text
        out += [p.rstrip("/").split("/")[-1] for p in re.findall(r"<Prefix>([^<]+)</Prefix>", text)[1:]]
        if "<IsTruncated>true" not in text:
            return sorted(s for s in set(out) if s.endswith("USDT") and s not in STABLES)
        marker = re.findall(r"<NextMarker>([^<]+)</NextMarker>", text)[0]


class RemovedSymbol(Exception):
    """The API doesn't know this symbol any more: use the archive."""


class Universe:
    def __init__(self, path: str | Path = "data/universe.sqlite", session: requests.Session | None = None):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)
        self.http = session or requests.Session()

    def refresh_symbols(self) -> int:
        info = {s["symbol"]: s for s in self.http.get(f"{REST}/fapi/v1/exchangeInfo", timeout=30).json()["symbols"]}
        # The archive lags new listings by up to a month, so add what the exchange lists today.
        listed = [s for s in info if s.endswith("USDT") and s not in STABLES]
        for s in sorted(set(archive_symbols(self.http)) | set(listed)):
            i = info.get(s, {})
            self.db.execute("INSERT INTO usymbols (symbol, status, underlying_type) VALUES (?,?,?) ON CONFLICT(symbol) "
                            "DO UPDATE SET status=excluded.status, underlying_type=excluded.underlying_type",
                            (s, i.get("status", "GONE"), i.get("underlyingType", "UNKNOWN")))
        self.db.commit()
        return self.db.execute("SELECT COUNT(*) FROM usymbols").fetchone()[0]

    def _get(self, path: str, params: dict) -> list:
        for attempt in range(6):
            r = self.http.get(f"{REST}{path}", params=params, timeout=30)
            if r.status_code in (418, 429):                     # rate limited: wait as told
                time.sleep(int(r.headers.get("Retry-After", 60)))
                continue
            if r.status_code >= 500:
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code == 400:
                raise RemovedSymbol(params.get("symbol"))
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"giving up on {path} {params}")

    def _archive_files(self, prefix: str) -> list[str]:
        """Zip files under an archive folder, as paths relative to VISION_BASE."""
        text = self.http.get(ARCHIVE, params={"prefix": f"data/futures/um/monthly/{prefix}"}, timeout=60).text
        keys = re.findall(r"<Key>([^<]+\.zip)</Key>", text)
        return [k.split("data/futures/um/monthly/")[1] for k in keys]

    def _archive_csv(self, rel: str) -> str:
        resp = self.http.get(f"{VISION_BASE}/{rel}", timeout=60)
        resp.raise_for_status()
        verify_checksum(resp.content, self.http.get(f"{VISION_BASE}/{rel}.CHECKSUM", timeout=60).text)
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            return zf.read(zf.namelist()[0]).decode("utf-8")

    def _archive_bars(self, s: str) -> list[tuple]:
        rows = []
        for rel in self._archive_files(f"klines/{s}/1d/"):
            for rec in csv.reader(io.StringIO(self._archive_csv(rel))):
                if rec and rec[0].strip().isdigit():              # skips the header row
                    rows.append((s, to_ms(rec[0]), float(rec[1]), float(rec[2]), float(rec[3]), float(rec[4]),
                                 float(rec[5]), float(rec[7])))
        return rows

    def _archive_funding(self, s: str) -> list[tuple]:
        rows = []
        for rel in self._archive_files(f"fundingRate/{s}/"):
            rows += [(s, t, rate) for t, _, rate in parse_funding_csv(self._archive_csv(rel))]
        return rows

    def download_bars(self, pause: float = 0.3) -> None:
        """Daily klines for every symbol not done yet (1500 per call, weight 10)."""
        now = int(time.time() * 1000)
        todo = [r[0] for r in self.db.execute("SELECT symbol FROM usymbols WHERE bars_done=0 ORDER BY symbol")]
        for n, s in enumerate(todo):
            start, rows = 0, []
            while True:
                try:
                    data = self._get("/fapi/v1/klines", {"symbol": s, "interval": "1d", "startTime": start,
                                                         "limit": 1500})
                except RemovedSymbol:
                    rows = self._archive_bars(s)
                    break
                rows += [(s, int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]), float(k[7]))
                         for k in data if int(k[6]) < now]               # closed bars only
                time.sleep(pause)
                if len(data) < 1500:
                    break
                start = int(data[-1][0]) + DAY_MS
            self.db.executemany("INSERT OR REPLACE INTO ubars VALUES (?,?,?,?,?,?,?,?)", rows)
            self.db.execute("UPDATE usymbols SET bars_done=1 WHERE symbol=?", (s,))
            self.db.commit()
            if n % 50 == 0:
                log.info("bars %d/%d", n, len(todo))

    def download_funding(self, symbols: list[str], pause: float = 0.7) -> None:
        """Funding for the symbols that matter (this endpoint allows ~100 calls a minute)."""
        placeholders = ",".join("?" * len(symbols))
        todo = [r[0] for r in self.db.execute(f"SELECT symbol FROM usymbols WHERE funding_done=0 AND symbol IN "
                                              f"({placeholders}) ORDER BY symbol", symbols)]
        for n, s in enumerate(todo):
            start, rows = 0, []
            while True:
                try:
                    data = self._get("/fapi/v1/fundingRate", {"symbol": s, "startTime": start, "limit": 1000})
                except RemovedSymbol:
                    rows = self._archive_funding(s)
                    break
                rows += [(s, int(f["fundingTime"]), float(f["fundingRate"])) for f in data]
                time.sleep(pause)
                if len(data) < 1000:
                    break
                start = int(data[-1]["fundingTime"]) + 1
            self.db.executemany("INSERT OR REPLACE INTO ufunding VALUES (?,?,?)", rows)
            self.db.execute("UPDATE usymbols SET funding_done=1 WHERE symbol=?", (s,))
            self.db.commit()
            if n % 25 == 0:
                log.info("funding %d/%d", n, len(todo))

    def top_up(self, pause: float = 0.15) -> int:
        """Daily job: closed candles since the last stored one, for every coin still trading and any
        coin that stopped only recently. A coin briefly not 'TRADING' must get its candles back."""
        now, added = int(time.time() * 1000), 0
        recent = now - 10 * DAY_MS
        names = [r[0] for r in self.db.execute(
            "SELECT symbol FROM usymbols WHERE underlying_type IN ('COIN', 'UNKNOWN') AND (status='TRADING' OR symbol "
            "IN (SELECT symbol FROM ubars GROUP BY symbol HAVING MAX(open_time) >= ?))", (recent,))]
        for s in names:
            last = self.db.execute("SELECT MAX(open_time) FROM ubars WHERE symbol=?", (s,)).fetchone()[0]
            start = last + DAY_MS if last else 0
            if start + DAY_MS > now:
                continue
            try:
                data = self._get("/fapi/v1/klines", {"symbol": s, "interval": "1d", "startTime": start, "limit": 1500})
            except RemovedSymbol:
                continue
            rows = [(s, int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]), float(k[7]))
                    for k in data if int(k[6]) < now]
            self.db.executemany("INSERT OR REPLACE INTO ubars VALUES (?,?,?,?,?,?,?,?)", rows)
            added += len(rows)
            time.sleep(pause)
        self.db.commit()
        return added

    def top_up_funding(self, symbols: list[str], pause: float = 0.7) -> int:
        """Daily job: funding since the last stored payment; coins never fetched get their full history."""
        self.download_funding(symbols)
        added = 0
        for s in symbols:
            last = self.db.execute("SELECT MAX(time) FROM ufunding WHERE symbol=?", (s,)).fetchone()[0]
            if last is None:
                continue
            try:
                data = self._get("/fapi/v1/fundingRate", {"symbol": s, "startTime": last + 1, "limit": 1000})
            except RemovedSymbol:
                continue
            self.db.executemany("INSERT OR REPLACE INTO ufunding VALUES (?,?,?)",
                                [(s, int(f["fundingTime"]), float(f["fundingRate"])) for f in data])
            added += len(data)
            time.sleep(pause)
        self.db.commit()
        return added

    def bars(self, symbol: str) -> pd.DataFrame:
        df = pd.read_sql_query("SELECT open_time, open, high, low, close, volume, quote_volume FROM ubars "
                               "WHERE symbol=? ORDER BY open_time", self.db, params=(symbol,))
        df.index = pd.to_datetime(df.pop("open_time"), unit="ms", utc=True)
        return df

    def funding(self, symbol: str) -> pd.DataFrame:
        df = pd.read_sql_query("SELECT time, rate FROM ufunding WHERE symbol=? ORDER BY time", self.db, params=(symbol,))
        df.index = pd.to_datetime(df.pop("time"), unit="ms", utc=True)
        return df
