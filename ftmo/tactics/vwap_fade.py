"""VWAP stretch fade.

HYPOTHESIS: away from the open, short-lived order pressure pushes the index unusually far from the day's average price,
and that stretch tends to snap back as liquidity providers step in. This is the opposite of a breakout: it bets against
the move, and its known weakness is a strong one-way day.

Rules: check every half-hour from 11:00 to 15:00 New York. Fade a move past 2 times the usual distance from VWAP for that
time of day, exit at VWAP or at 15:30 at the latest. VWAP uses tick counts, as CFDs have no exchange volume.
"""

from dataclasses import dataclass

import numpy as np

SETTINGS = {"lookback_days": 14, "stretch_multiple": 2.0, "hard_stop_multiple": 2.0}
TUNABLE = ("lookback_days", "stretch_multiple")

CHECK_MINUTES = tuple(range(90, 361, 30))  # 11:00 to 15:30 New York
LAST_MINUTE = 359  # the 15:29 bar: close anything open, enter nothing
PAPER_VOL_TARGET = 0.02  # same sizing frame as the other tactics
PAPER_MAX_EXPOSURE = 4.0


@dataclass
class Levels:
    upper: np.ndarray
    lower: np.ndarray
    vwap: np.ndarray
    hard_stop: np.ndarray
    ready: np.ndarray


def levels(days, settings: dict) -> Levels:
    lookback = int(round(settings["lookback_days"]))
    typical = (days.high + days.low + days.close) / 3
    weight = np.maximum(days.ticks, 1.0)
    vwap = np.cumsum(typical * weight, axis=1) / np.cumsum(weight, axis=1)

    # Usual distance from VWAP at each minute, from earlier days only, as a share of price
    distance = np.abs(days.close / vwap - 1)
    usual = np.full(distance.shape, np.nan)
    for d in range(lookback, len(distance)):
        usual[d] = distance[d - lookback:d].mean(axis=0)

    stretch = settings["stretch_multiple"] * usual
    return Levels(
        upper=vwap * (1 + stretch), lower=vwap * (1 - stretch), vwap=vwap,
        hard_stop=settings["hard_stop_multiple"] * usual[:, -1],
        ready=~np.isnan(usual[:, -1]),
    )


def decide(day: int, minute: int, price: float, position: int, lv: Levels) -> int:
    if minute >= LAST_MINUTE:
        return 0
    if price > lv.upper[day, minute]:
        return -1
    if price < lv.lower[day, minute]:
        return 1
    if position > 0 and price >= lv.vwap[day, minute]:
        return 0
    if position < 0 and price <= lv.vwap[day, minute]:
        return 0
    return position
