"""US100 held overnight, Monday to Thursday.

HYPOTHESIS: much of the long-run gain in US stock indices comes between the close and the next open, not during the day
(Cliff, Cooper and Gulen 2008; Lou, Polk and Skouras 2019). Holding US100 over those hours should earn that drift, at
times when noise_area is flat, so its good days should differ (owner-approved, 2026-11-08).

Rules, fixed before any test:
- Buy at the open of the last session minute (15:59 New York). Sell at the next session's open (09:30).
- Only when the next session is the next calendar day: never over a weekend or a market holiday.
- Broker stop 2 times the average close-to-open move of the last 14 nights (noise_area's multiple and lookback).
- Costs: 0.75 of the spread on each fill, as everywhere, plus FTMO's swap per lot per night, read from MT5 on
  2026-11-08: a long pays 6.56 points, a short earns 0.21. Swap follows interest rates, so this overcharges the
  low-rate years before 2023.
- Long only, no filters. Shorts appear only in the lab's random-direction luck check.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ftmo.backtest import DATA_DIR, SERVER_HOURS_AHEAD_OF_NY, SLIPPAGE_SHARE, Result, position_sizes

SYMBOL = "US100.cash"
SETTINGS = {"lookback_days": 14, "stop_multiple": 2.0}
TUNABLE = ("lookback_days", "stop_multiple")
SWAP_POINTS = {1: -6.56, -1: 0.21}  # per lot per night, by direction
ENTRY_MINUTE = 389  # 15:59 New York
CHECK_MINUTES = (ENTRY_MINUTE,)
PAPER_VOL_TARGET = 0.02  # same sizing frame as the other tactics
PAPER_MAX_EXPOSURE = 4.0


@dataclass
class Levels:
    stop: np.ndarray  # stop distance per day, as a share of the entry price
    ready: np.ndarray


def levels(days, settings: dict) -> Levels:
    """Uses this morning's open and earlier closes only, all known by 15:59."""
    lookback = int(round(settings["lookback_days"]))
    gap = np.abs(days.open[1:, 0] / days.close[:-1, -1] - 1)  # gap[k]: the night that ends at day k + 1's open
    stop = np.full(len(days.dates), np.nan)
    for d in range(lookback, len(days.dates)):
        stop[d] = settings["stop_multiple"] * gap[d - lookback:d].mean()
    return Levels(stop, ~np.isnan(stop))


def decide(day: int, minute: int, price: float, position: int, lv: Levels) -> int:
    return position  # trades are replayed in simulate


def night_bars(days) -> pd.DataFrame:
    """Every bar in the tested period, in New York time, so a stop can be checked through the night."""
    bars = pd.read_pickle(DATA_DIR / f"{SYMBOL}_M1.pkl")
    bars = bars.assign(ny=bars["time"] - pd.Timedelta(hours=SERVER_HOURS_AHEAD_OF_NY))
    keep = (bars["ny"] >= days.dates[0]) & (bars["ny"] < days.dates[-1] + pd.Timedelta(days=1))
    return bars[keep]


def simulate(days, settings: dict, target_daily_vol: float, cost_mult: float = 1.0,
             random_seed: int | None = None) -> Result:
    rng = None if random_seed is None else np.random.default_rng(random_seed)
    lv = levels(days, settings)
    sizes = position_sizes(days, target_daily_vol, target_daily_vol / PAPER_VOL_TARGET * PAPER_MAX_EXPOSURE)
    bars = night_bars(days)
    times, opens = bars["ny"].to_numpy(), bars["open"].to_numpy()
    lows, highs = bars["low"].to_numpy(), bars["high"].to_numpy()
    returns, worst = np.zeros(len(sizes)), np.zeros(len(sizes))
    trades = 0

    for d in np.flatnonzero((sizes > 0) & lv.ready):
        if d + 1 >= len(days.dates) or days.dates[d + 1] - days.dates[d] != pd.Timedelta(days=1):
            continue
        size = sizes[d]
        direction = 1 if rng is None else int(rng.choice([-1, 1]))
        entry, exit_price = days.open[d, ENTRY_MINUTE], days.open[d + 1, 0]
        stop = entry * (1 - direction * lv.stop[d])

        start = np.searchsorted(times, np.datetime64(days.dates[d] + pd.Timedelta(minutes=15 * 60 + 59)))
        end = np.searchsorted(times, np.datetime64(days.dates[d + 1] + pd.Timedelta(minutes=9 * 60 + 30)))
        night_low = lows[start:end].min() if end > start else entry
        night_high = highs[start:end].max() if end > start else entry
        hit = np.flatnonzero(lows[start:end] <= stop if direction > 0 else highs[start:end] >= stop)
        if hit.size:
            first = opens[start + hit[0]]
            exit_price = min(first, stop) if direction > 0 else max(first, stop)  # a gap through the stop fills worse

        spread_in, spread_out = days.spread[d, ENTRY_MINUTE], days.spread[d + 1, 0]
        costs = size * cost_mult * ((0.5 + SLIPPAGE_SHARE) * (spread_in + spread_out) - SWAP_POINTS[direction]) / entry
        pnl = direction * size * (exit_price - entry) / entry - costs
        adverse = night_low if direction > 0 else night_high
        returns[d] = pnl
        worst[d] = min(pnl, direction * size * (adverse - entry) / entry - costs)
        trades += 1
    return Result(days.dates, returns, worst, trades)
