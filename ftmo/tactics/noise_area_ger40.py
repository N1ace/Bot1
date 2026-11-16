"""The noise_area rules, unchanged, on GER40 during the Xetra session.

HYPOTHESIS: a break out of the day's normal range reflects real order flow that runs into the close. If that is a market
effect and not a fit to US100, it should also hold on the DAX in its own hours, which mostly don't overlap US hours.
That would make its good days differ from noise_area's, the reason for testing it (owner-approved, 2026-11-08).

Nothing is tuned for GER40. Settings and the half-hour checks are noise_area's. Its session is the Xetra day,
09:00 to 17:30 Frankfurt, so checks run from 09:30 to 17:00 and the close-out is 17:29.
Costs: GER40's spread floor as a share of price. Commission assumed zero, as for the DAX tactic, not read from MT5.
"""

from ftmo.tactics.noise_area import (  # noqa: F401
    PAPER_MAX_EXPOSURE, PAPER_VOL_TARGET, SETTINGS, TUNABLE, decide, levels,
)

SYMBOL = "GER40.cash"
INFO_SYMBOL = None
SESSION = ("Europe/Berlin", 9 * 60, 510, 500)  # zone, first minute, minutes in the session, bars needed for a full day
CHECK_MINUTES = tuple(range(30, 510, 30))
