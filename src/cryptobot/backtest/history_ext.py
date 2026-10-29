"""Extended history: backtests from August 2017 instead of January 2020.

Why: 2020-2026 is basically one long cycle. Adding 2017-2019 brings in the 2018
bear market (BTC -84%), a very different regime, and more walk-forward windows.

How the pieces are joined (per symbol):
  prices   Binance USDT-M futures candles where they exist (2020-01 on),
           Binance SPOT candles before that (from 2017-08). Spot and perp prices
           differ by ~0.1% normally; good enough for a daily strategy. Spot candles
           also stand in for the mark price before 2020.
  funding  1. Binance futures funding (BTC from 2019-09, ETH from 2019-11)
           2. Bybit inverse perpetual funding (from late 2018) - another exchange
              and contract type, a reasonable proxy
           3. before that: ASSUMED 0.01% every 8h (Binance's baseline, ~11%/yr for longs)
  Every funding row keeps its `source`, so results can say how much was assumed.

BitMEX (funding since 2016) would be better for 2017-2018, but its API is blocked from here.
"""

from __future__ import annotations

import logging
import sqlite3

import pandas as pd
import requests

from cryptobot.backtest.data import MarketDataStore

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS funding_ext (
    symbol TEXT NOT NULL,           -- our symbol name, e.g. BTCUSDT
    time INTEGER NOT NULL,          -- ms since epoch, UTC
    rate REAL NOT NULL,
    source TEXT NOT NULL,           -- binance_rest / bybit_inverse
    PRIMARY KEY (symbol, time, source)
);
"""

BYBIT_SYMBOL = {"BTCUSDT": "BTCUSD", "ETHUSDT": "ETHUSD"}
SPOT_START = "2017-08-17"            # first full Binance spot day for BTC/ETH USDT
ASSUMED_RATE = 0.0001                # per 8h


def _ms(ts) -> int:
    return int(pd.Timestamp(ts).timestamp() * 1000)


class ExtendedHistory:
    def __init__(self, store: MarketDataStore, db_path: str, session: requests.Session | None = None):
        self.store = store
        self.db_path = db_path
        self.http = session or requests.Session()
        with sqlite3.connect(db_path) as con:
            con.executescript(SCHEMA)

    # ---- download ------------------------------------------------------------------

    def update(self, symbols: list[str]) -> dict[str, int]:
        added = {"spot_rows": self.store.update_spot(symbols)}
        for s in symbols:
            added[f"{s}_binance_early"] = self._binance_early_funding(s)
            added[f"{s}_bybit"] = self._bybit_funding(s)
        return added

    def _binance_early_funding(self, symbol: str) -> int:
        """Binance futures funding before our monthly files start (Sep-Dec 2019)."""
        first_file = self.store.funding(symbol).index.min()
        rows, start = [], _ms("2019-01-01")
        while True:
            resp = self.http.get("https://fapi.binance.com/fapi/v1/fundingRate", timeout=30,
                                 params={"symbol": symbol, "startTime": start, "limit": 1000})
            resp.raise_for_status()
            data = resp.json()
            batch = [r for r in data if pd.Timestamp(int(r["fundingTime"]), unit="ms", tz="UTC") < first_file]
            rows += [(symbol, int(r["fundingTime"]), float(r["fundingRate"]), "binance_rest") for r in batch]
            if len(data) < 1000 or len(batch) < len(data):
                break
            start = int(data[-1]["fundingTime"]) + 1
        return self._insert(rows)

    def _bybit_funding(self, symbol: str) -> int:
        """Bybit inverse perpetual funding up to 2019-12, paging backwards (200 per page)."""
        bybit = BYBIT_SYMBOL.get(symbol)
        if not bybit:
            return 0
        rows, end = [], _ms("2020-01-01")
        while True:
            resp = self.http.get("https://api.bybit.com/v5/market/funding/history", timeout=30,
                                 params={"category": "inverse", "symbol": bybit, "endTime": end, "limit": 200})
            resp.raise_for_status()
            page = resp.json().get("result", {}).get("list", [])
            if not page:
                break
            rows += [(symbol, int(r["fundingRateTimestamp"]), float(r["fundingRate"]), "bybit_inverse") for r in page]
            oldest = min(int(r["fundingRateTimestamp"]) for r in page)
            if len(page) < 200 or oldest >= end:
                break
            end = oldest - 1
        return self._insert(rows)

    def _insert(self, rows: list[tuple]) -> int:
        with sqlite3.connect(self.db_path) as con:
            before = con.execute("SELECT COUNT(*) FROM funding_ext").fetchone()[0]
            con.executemany("INSERT OR REPLACE INTO funding_ext VALUES (?,?,?,?)", rows)
            return con.execute("SELECT COUNT(*) FROM funding_ext").fetchone()[0] - before

    # ---- joined series --------------------------------------------------------------

    def bars(self, symbol: str, kind: str = "trade") -> pd.DataFrame:
        """Futures candles ('trade' or 'mark') with spot candles in front of them."""
        fut = self.store.bars(symbol, kind)
        spot = self.store.bars(symbol, "spot")
        cutoff = fut.index.min() if len(fut) else spot.index.max() + pd.Timedelta(days=1)
        spot = spot[(spot.index >= pd.Timestamp(SPOT_START, tz="UTC")) & (spot.index < cutoff)]
        return pd.concat([spot, fut]).sort_index()

    def funding(self, symbol: str) -> pd.DataFrame:
        """Binance funding + earlier proxies + assumed baseline, with a `source` column."""
        main = self.store.funding(symbol).assign(source="binance")
        with sqlite3.connect(self.db_path) as con:
            ext = pd.read_sql_query("SELECT time, rate, source FROM funding_ext WHERE symbol=? ORDER BY time",
                                    con, params=(symbol,))
        ext.index = pd.to_datetime(ext.pop("time"), unit="ms", utc=True)
        first = main.index.min()
        early = ext[(ext["source"] == "binance_rest") & (ext.index < first)]
        if len(early):
            first = early.index.min()
        bybit = ext[(ext["source"] == "bybit_inverse") & (ext.index < first)]
        if len(bybit):
            first = bybit.index.min()
        assumed_idx = pd.date_range(pd.Timestamp(SPOT_START, tz="UTC"), first - pd.Timedelta(hours=1), freq="8h")
        assumed = pd.DataFrame({"rate": ASSUMED_RATE, "source": "assumed"}, index=assumed_idx)
        out = pd.concat([assumed, bybit, early, main]).sort_index()
        out["interval_hours"] = out["interval_hours"].fillna(8) if "interval_hours" in out else 8
        return out[["interval_hours", "rate", "source"]]

    def coverage(self, symbol: str) -> list[str]:
        """Human-readable list of which source covers which dates (printed with results)."""
        f, b, fut = self.funding(symbol), self.bars(symbol), self.store.bars(symbol, "trade")
        lines = [f"{symbol} prices: spot {b.index.min():%Y-%m-%d} -> futures from {fut.index.min():%Y-%m-%d}"]
        for src in ("assumed", "bybit_inverse", "binance_rest", "binance"):
            sel = f.index[f["source"] == src]
            if len(sel):
                lines.append(f"{symbol} funding {src}: {sel.min():%Y-%m-%d} -> {sel.max():%Y-%m-%d} ({len(sel)})")
        return lines
