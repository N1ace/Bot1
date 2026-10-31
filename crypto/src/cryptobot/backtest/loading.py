"""Load backtest inputs (bars, mark bars, funding) - normal (2020+) or extended (2017+) history."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from cryptobot.backtest.data import MarketDataStore
from cryptobot.backtest.history_ext import ExtendedHistory


@dataclass
class History:
    bars: dict[str, pd.DataFrame]
    marks: dict[str, pd.DataFrame]
    funding: dict[str, pd.DataFrame]
    btc: pd.DataFrame
    notes: list[str] = field(default_factory=list)   # where the data comes from (printed with results)


def load_history(symbols: list[str], db: str = "data/market.sqlite", extended: bool = False) -> History:
    store = MarketDataStore(db)
    try:
        if extended:
            ext = ExtendedHistory(store, db)
            src = lambda s, kind: ext.bars(s, kind)          # noqa: E731
            funding = {s: ext.funding(s) for s in symbols}
            notes = [line for s in symbols for line in ext.coverage(s)]
        else:
            src = lambda s, kind: store.bars(s, kind)        # noqa: E731
            funding = {s: store.funding(s) for s in symbols}
            notes = ["Binance USDT-M futures data only (2020-01 on)"]
        bars = {s: src(s, "trade") for s in symbols}
        marks = {s: src(s, "mark") for s in symbols}
        btc = bars["BTCUSDT"] if "BTCUSDT" in bars else src("BTCUSDT", "trade")
    finally:
        store.close()
    if any(df.empty for df in bars.values()):
        raise RuntimeError("Missing data - run scripts/download_history.py" + (" --extended" if extended else ""))
    return History(bars, marks, funding, btc, notes)
