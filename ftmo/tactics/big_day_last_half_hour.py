"""Big days carry into the close (Baltussen, Da, Lammers and Martens, Journal of Financial Economics, 2021).

HYPOTHESIS: when the index has moved a lot by mid-afternoon, options dealers who must hedge trade in the same direction
late in the day, pushing the move further.

This uses the whole day's move, not the first half-hour, and trades only unusually large days. Rules: at 15:30 New York,
take the move since yesterday's close. If it is larger than its average of the last 14 days, trade in its direction until
one minute before the close. The hard stop comes from this half-hour on earlier days.
"""

from dataclasses import dataclass

import numpy as np

SETTINGS = {"lookback_days": 14, "size_multiple": 1.0, "hard_stop_multiple": 3.0}
TUNABLE = ("lookback_days", "size_multiple")

CHECK_MINUTES = (360,)  # 15:30 New York
SIGNAL_MINUTE = 359  # the 15:29 bar
PAPER_VOL_TARGET = 0.02  # same sizing frame as the other tactics
PAPER_MAX_EXPOSURE = 4.0


@dataclass
class Levels:
    direction: np.ndarray
    hard_stop: np.ndarray
    ready: np.ndarray


def levels(days, settings: dict) -> Levels:
    lookback = int(round(settings["lookback_days"]))
    previous_close = np.r_[np.nan, days.close[:-1, -1]]
    move = days.close[:, SIGNAL_MINUTE] / previous_close - 1
    last_move = np.abs(days.open[:, -1] / days.open[:, CHECK_MINUTES[0]] - 1)

    n = len(move)
    direction, hard_stop = np.zeros(n), np.full(n, np.nan)
    for d in range(lookback + 1, n):
        usual = np.abs(move[d - lookback:d]).mean()
        direction[d] = np.sign(move[d]) if abs(move[d]) > settings["size_multiple"] * usual else 0
        hard_stop[d] = settings["hard_stop_multiple"] * last_move[d - lookback:d].mean()

    return Levels(direction, hard_stop, ~np.isnan(hard_stop))


def decide(day: int, minute: int, price: float, position: int, lv: Levels) -> int:
    return int(lv.direction[day])
