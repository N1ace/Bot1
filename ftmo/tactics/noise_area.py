"""Noise-area momentum with a VWAP stop (Zarattini, Aziz and Barbon).

HYPOTHESIS: on most days an index wanders inside a normal range around the open. A move past that range, sized from the
same minute of the last 14 days, signals real order flow that tends to continue into the close.

Rules were fixed from the paper before any FTMO test. Our changes, each needed for FTMO:
- VWAP uses tick counts, because CFDs have no exchange volume.
- A hard stop sits at the broker, as the paper's half-hour checks can't protect a trade between checks.
- Flat one minute before the US close, and no filters from the paper's regime results.
- Not yet included: the flat period around FOMC, which the live bot needs. Challenges reportedly don't require it.
"""

from dataclasses import dataclass

import numpy as np

SETTINGS = {"lookback_days": 14, "band_multiplier": 1.0, "hard_stop_multiple": 2.0}
# Lab's stability check moves these by 20%, and they count as the tuned settings
TUNABLE = ("lookback_days", "band_multiplier")

CHECK_MINUTES = tuple(range(30, 390, 30))  # 10:00 to 15:30 New York
PAPER_VOL_TARGET = 0.02
PAPER_MAX_EXPOSURE = 4.0


@dataclass
class Levels:
    upper: np.ndarray
    lower: np.ndarray
    vwap: np.ndarray
    hard_stop: np.ndarray  # stop distance per day, as a share of the entry price
    ready: np.ndarray


def levels(days, settings: dict) -> Levels:
    lookback = int(round(settings["lookback_days"]))
    day_open = days.open[:, 0]
    move = np.abs(days.close / day_open[:, None] - 1)

    # Average move at each minute over earlier days only
    sigma = np.full(move.shape, np.nan)
    for d in range(lookback, len(move)):
        sigma[d] = move[d - lookback:d].mean(axis=0)
    sigma *= settings["band_multiplier"]

    previous_close = np.r_[np.nan, days.close[:-1, -1]]
    top = np.fmax(day_open, previous_close)[:, None]
    bottom = np.fmin(day_open, previous_close)[:, None]

    typical = (days.high + days.low + days.close) / 3
    weight = np.maximum(days.ticks, 1.0)
    vwap = np.cumsum(typical * weight, axis=1) / np.cumsum(weight, axis=1)

    return Levels(
        upper=top * (1 + sigma), lower=bottom * (1 - sigma), vwap=vwap,
        hard_stop=settings["hard_stop_multiple"] * sigma[:, -1],
        ready=~np.isnan(sigma[:, -1]) & ~np.isnan(previous_close),
    )


def decide(day: int, minute: int, price: float, position: int, lv: Levels) -> int:
    """Target position after a check, using only the bar that has just closed."""
    upper, lower, vwap = lv.upper[day, minute], lv.lower[day, minute], lv.vwap[day, minute]
    if price > upper:
        return 1
    if price < lower:
        return -1
    if position > 0 and price < max(upper, vwap):
        return 0
    if position < 0 and price > min(lower, vwap):
        return 0
    return position
