"""First half-hour predicts the last half-hour (Gao, Han, Li and Zhou, Journal of Financial Economics, 2018).

HYPOTHESIS: traders who adjust positions late in the day, and those hedging, tend to act in the direction the day
started. So the return from yesterday's close to 10:00 New York sets the direction of the last half-hour.

Rules fixed from the paper before any FTMO test: one trade a day, entered at 15:30 and closed one minute before the US
close. No filter for volatile days, even though the paper found the effect stronger on them. Our addition: a hard stop
at the broker, sized from the same half-hour on earlier days.
"""

from dataclasses import dataclass

import numpy as np

SETTINGS = {"lookback_days": 14, "hard_stop_multiple": 3.0}
TUNABLE = ("lookback_days", "hard_stop_multiple")

CHECK_MINUTES = (360,)  # 15:30 New York
FIRST_HALF_HOUR_END = 29  # the 09:59 bar
PAPER_VOL_TARGET = 0.02  # same sizing frame as the first tactic, for a fair comparison
PAPER_MAX_EXPOSURE = 4.0


@dataclass
class Levels:
    direction: np.ndarray
    hard_stop: np.ndarray
    ready: np.ndarray


def levels(days, settings: dict) -> Levels:
    lookback = int(round(settings["lookback_days"]))
    previous_close = np.r_[np.nan, days.close[:-1, -1]]
    direction = np.sign(days.close[:, FIRST_HALF_HOUR_END] / previous_close - 1)

    # Size of this half-hour on earlier days only
    last_move = np.abs(days.open[:, -1] / days.open[:, CHECK_MINUTES[0]] - 1)
    hard_stop = np.full(len(last_move), np.nan)
    for d in range(lookback, len(last_move)):
        hard_stop[d] = settings["hard_stop_multiple"] * last_move[d - lookback:d].mean()

    return Levels(direction, hard_stop, ~np.isnan(hard_stop) & ~np.isnan(direction))


def decide(day: int, minute: int, price: float, position: int, lv: Levels) -> int:
    return int(lv.direction[day])
