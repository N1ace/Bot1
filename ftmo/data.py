"""Download 1-minute bid and ask prices from Dukascopy's free feed.

These are one broker's CFD prices, not FTMO's, so spreads here are only a guide. The feed times out often, so every
day is saved as its own file: a stopped download continues where it left off, and re-running only asks for what's missing.

    python -m ftmo.data US500 2026-10-19 2026-10-23
"""

import argparse
import lzma
import struct
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

BASE_URL = "https://datafeed.dukascopy.com/datafeed"
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "dukascopy"

# Our name -> the feed's name
SYMBOLS = {"US100": "USATECHIDXUSD", "US500": "USA500IDXUSD", "XAUUSD": "XAUUSD"}

# Prices arrive as whole numbers. Check a sample against a real price before trusting a new symbol.
PRICE_SCALE = 1000
BAR = struct.Struct(">5If")  # seconds into the day, open, close, low, high (note the order), volume

TIMEOUT_S = 15
TRIES = 5


def day_url(feed_symbol: str, day: date, side: str) -> str:
    # The feed counts months from zero, so October is 09
    return f"{BASE_URL}/{feed_symbol}/{day.year}/{day.month - 1:02d}/{day.day:02d}/{side}_candles_min_1.bi5"


def parse_day(raw: bytes, day: date) -> pd.DataFrame:
    """Turn one compressed day into bars. An empty file means the market was closed."""
    columns = ["time", "open", "high", "low", "close", "volume"]
    if not raw:
        return pd.DataFrame(columns=columns)
    data = lzma.decompress(raw, format=lzma.FORMAT_ALONE)
    midnight = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    rows = []
    for seconds, o, c, low, high, volume in BAR.iter_unpack(data):
        rows.append((midnight + timedelta(seconds=seconds), o / PRICE_SCALE, high / PRICE_SCALE,
                     low / PRICE_SCALE, c / PRICE_SCALE, volume))
    return pd.DataFrame(rows, columns=columns)


def fetch(url: str) -> bytes | None:
    """Return the file, b"" if the feed has nothing for that day, or None if every try failed."""
    for attempt in range(TRIES):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            return urllib.request.urlopen(request, timeout=TIMEOUT_S).read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return b""
        except OSError:
            pass
        time.sleep(2**attempt)
    return None


def day_path(symbol: str, day: date, side: str) -> Path:
    return DATA_DIR / symbol / str(day.year) / f"{day:%m-%d}_{side}.bi5"


def download(symbol: str, start: date, end: date) -> tuple[int, int, int]:
    """Fetch every missing day from start to end. Returns (fetched, already saved, failed)."""
    fetched = saved = failed = 0
    day = start
    while day <= end:
        # Saturdays have no prices on these markets
        if day.weekday() != 5:
            for side in ("BID", "ASK"):
                path = day_path(symbol, day, side)
                if path.exists():
                    saved += 1
                    continue
                raw = fetch(day_url(SYMBOLS[symbol], day, side))
                if raw is None:
                    failed += 1
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                # Write then rename, so a stopped run can't leave half a file that looks finished
                partial = path.with_suffix(".part")
                partial.write_bytes(raw)
                partial.replace(path)
                fetched += 1
        day += timedelta(days=1)
    return fetched, saved, failed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol", choices=SYMBOLS)
    parser.add_argument("start", type=date.fromisoformat)
    parser.add_argument("end", type=date.fromisoformat)
    args = parser.parse_args()
    started = time.monotonic()
    fetched, saved, failed = download(args.symbol, args.start, args.end)
    print(f"{args.symbol}: fetched {fetched}, already saved {saved}, failed {failed}, "
          f"{time.monotonic() - started:.0f}s")


if __name__ == "__main__":
    main()
