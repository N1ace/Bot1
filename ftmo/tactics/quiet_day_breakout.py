"""Breakout of yesterday's range after a quiet day.

HYPOTHESIS: markets alternate between calm and active. After a narrower day than usual, a break of yesterday's high or
low is more likely to start a real move. The noise-area paper also found its best days followed narrow ranges.

Rules: only trade if yesterday's session range was below its average of the 14 days before it. Check every half-hour from
10:00 to 15:30 New York. Long above yesterday's high, short below its low, held to the close. The hard stop equals
yesterday's range. After a stop, a new break can enter again.
"""

from dataclasses import dataclass

import numpy as np

SETTINGS = {"lookback_days": 14, "quiet_multiple": 1.0, "hard_stop_multiple": 1.0}
TUNABLE = ("lookback_days", "quiet_multiple")

CHECK_MINUTES = tuple(range(30, 361, 30))  # 10:00 to 15:30 New York
PAPER_VOL_TARGET = 0.02  # same sizing frame as the other tactics
PAPER_MAX_EXPOSURE = 4.0


@dataclass
class Levels:
    high: np.ndarray
    low: np.ndarray
    quiet: np.ndarray
    hard_stop: np.ndarray
    ready: np.ndarray


def levels(days, settings: dict) -> Levels:
    lookback = int(round(settings["lookback_days"]))
    session_high, session_low = days.high.max(axis=1), days.low.min(axis=1)
    session_range = session_high - session_low

    n = len(session_range)
    high, low = np.full(n, np.nan), np.full(n, np.nan)
    quiet, hard_stop = np.zeros(n), np.full(n, np.nan)
    for d in range(lookback + 1, n):
        yesterday = d - 1
        high[d], low[d] = session_high[yesterday], session_low[yesterday]
        usual = session_range[yesterday - lookback:yesterday].mean()
        quiet[d] = session_range[yesterday] < settings["quiet_multiple"] * usual
        hard_stop[d] = settings["hard_stop_multiple"] * session_range[yesterday] / days.open[d, 0]

    return Levels(high, low, quiet, hard_stop, ~np.isnan(high))


def decide(day: int, minute: int, price: float, position: int, lv: Levels) -> int:
    if not lv.quiet[day] or position != 0:
        return position
    if price > lv.high[day]:
        return 1
    if price < lv.low[day]:
        return -1
    return 0
