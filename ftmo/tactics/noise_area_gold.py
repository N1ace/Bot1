"""The noise_area rules, unchanged, on gold during US hours.

HYPOTHESIS: if a break out of the day's normal range reflects real order flow, it should also work on a market we haven't
tested. This is the check on whether noise_area's result on US100 was a fit to that market.

Nothing is tuned for gold: settings, hours and the close-out are noise_area's. Costs use gold's commission and spread.
"""

from ftmo.tactics.noise_area import (  # noqa: F401
    CHECK_MINUTES, PAPER_MAX_EXPOSURE, PAPER_VOL_TARGET, SETTINGS, TUNABLE, decide, levels,
)

SYMBOL = "XAUUSD"
