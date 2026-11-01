"""Measure what each market did around each event, from FTMO's 1-minute bars."""

from pathlib import Path

import numpy as np
import pandas as pd

from news_engine.store import server_to_utc

BARS_DIR = Path(__file__).resolve().parent.parent / "data" / "ftmo"
SYMBOLS = ("US100.cash", "US500.cash", "GER40.cash")
WINDOWS = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}
MAX_GAP_MINUTES = 5  # no bar this close to a moment means the market wasn't open
VOL_DAYS = 20
FIRST_REAL_BAR = "2021-12-15"  # older history holds about one bar a day


def load_bars(symbol: str) -> pd.DataFrame:
    bars = pd.read_pickle(BARS_DIR / f"{symbol}_M1.pkl")
    bars = bars[bars["time"] >= FIRST_REAL_BAR]
    bars = bars.assign(utc=server_to_utc(bars["time"])).dropna(subset=["utc"])
    return bars.set_index("utc").sort_index()


def pre_volatility(bars: pd.DataFrame, before: pd.Timestamp) -> float | None:
    """Daily volatility, in percent, from the 20 days before the event only."""
    closes = bars.loc[:before - pd.Timedelta(minutes=1), "close"].resample("1D").last().dropna()
    if len(closes) < VOL_DAYS + 1:
        return None
    return float(closes.pct_change().tail(VOL_DAYS).std() * 100)


def react(bars: pd.DataFrame, moment: pd.Timestamp) -> dict:
    """Window -> (return %, high-low range %), from the last bar before the event."""
    before = bars.loc[moment - pd.Timedelta(minutes=MAX_GAP_MINUTES):moment - pd.Timedelta(minutes=1)]
    if before.empty:
        return {}
    start = float(before["close"].iloc[-1])
    found = {}
    for name, minutes in WINDOWS.items():
        after = bars.loc[moment:moment + pd.Timedelta(minutes=minutes - 1)]
        if len(after) < max(1, minutes * 0.8):
            continue
        high, low = float(after["high"].max()), float(after["low"].min())
        found[name] = ((float(after["close"].iloc[-1]) / start - 1) * 100,
                       (max(high, start) - min(low, start)) / start * 100)
    return found


def record_all(db) -> int:
    """Measure every stored event for every market that was open."""
    events = db.execute("SELECT id, scheduled_utc FROM events").fetchall()
    rows = []
    for symbol in SYMBOLS:
        bars = load_bars(symbol)
        last_bar = bars.index.max()
        for event_id, scheduled in events:
            moment = pd.Timestamp(scheduled)
            if moment >= last_bar:
                continue
            vol = pre_volatility(bars, moment)
            for window, (ret, width) in react(bars, moment).items():
                rows.append((event_id, symbol, window, ret, width, None if vol is None or np.isnan(vol) else vol))
    db.executemany("INSERT OR REPLACE INTO reactions VALUES (?, ?, ?, ?, ?, ?)", rows)
    db.commit()
    return len(rows)
