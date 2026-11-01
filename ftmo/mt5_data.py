"""Save FTMO's 1-minute history from a running, logged-in MT5 terminal.

Read-only: this file must never call order_send. MT5 stamps bars in server time, which we keep as it is.

    python -m ftmo.mt5_data
"""

from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "ftmo"
SYMBOLS = ("US100.cash", "US500.cash", "XAUUSD")
MAX_BARS = 3_000_000
POINT = 0.01  # MT5 stores the spread in points


def read_symbol(symbol: str) -> pd.DataFrame:
    if not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"{symbol}: not available in this terminal")
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, MAX_BARS)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"{symbol}: no bars ({mt5.last_error()})")
    bars = pd.DataFrame(rates)
    bars["time"] = pd.to_datetime(bars["time"], unit="s")
    bars["spread"] = bars["spread"] * POINT
    return bars[["time", "open", "high", "low", "close", "tick_volume", "spread"]]


def main() -> None:
    if not mt5.initialize():
        raise RuntimeError(f"Can't reach MT5: {mt5.last_error()}")
    try:
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
