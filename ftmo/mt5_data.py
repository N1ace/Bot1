"""Save FTMO's 1-minute history from a running, logged-in MT5 terminal.

Read-only: this file must never call order_send. MT5 stamps bars in server time, which we keep as it is.

    python -m ftmo.mt5_data
"""

import sys
from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "ftmo"
SYMBOLS = ("US100.cash", "US500.cash", "XAUUSD")
MAX_BARS = 3_000_000
LATEST_BARS = 30_000
LATEST_SYMBOLS = ("US100.cash", "US500.cash", "GER40.cash")
POINT = 0.01  # MT5 stores the spread in points


def read_symbol(symbol: str, count: int = MAX_BARS) -> pd.DataFrame:
    if not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"{symbol}: not available in this terminal")
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, count)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"{symbol}: no bars ({mt5.last_error()})")
    bars = pd.DataFrame(rates)
    bars["time"] = pd.to_datetime(bars["time"], unit="s")
    bars["spread"] = bars["spread"] * POINT
    return bars[["time", "open", "high", "low", "close", "tick_volume", "spread"]]


def add_latest(symbol: str) -> None:
    """Add recent bars to a saved history, for the news engine's daily job."""
    fresh = read_symbol(symbol, LATEST_BARS)
    path = DATA_DIR / f"{symbol}_M1.pkl"
    old = pd.read_pickle(path)
    pd.concat([old[old["time"] < fresh["time"].iloc[0]], fresh[list(old.columns)]], ignore_index=True).to_pickle(path)


def main() -> None:
    if not mt5.initialize():
        raise RuntimeError(f"Can't reach MT5: {mt5.last_error()}")
    try:
        if "--latest" in sys.argv:
            for symbol in LATEST_SYMBOLS:
                add_latest(symbol)
            return
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        for symbol in SYMBOLS:
            bars = read_symbol(symbol)
            path = DATA_DIR / f"{symbol}_M1.pkl"
            bars.to_pickle(path)
            print(f"{symbol}: {len(bars)} bars, {bars.time.min()} to {bars.time.max()}", flush=True)
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
