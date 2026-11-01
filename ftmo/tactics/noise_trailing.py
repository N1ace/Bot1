"""noise_area with a broker stop that tightens, tested as an exception approved by the owner on 2026-11-01.

HYPOTHESIS: noise_area can only react every half-hour, so a turn between checks gives back profit. Moving the broker stop
to the tactic's own exit level at each check should keep the same entries while cutting those give-backs.

This idea came from noise_area's weakness on this same data, so its results carry extra doubt, even if it passes. Rules:
entries, sizing, hard stop and close-out are noise_area's. At each check, including the entry, the stop moves to the
tighter of VWAP and the band, and it never moves back. A level past the next open is ignored, as a broker would refuse it.
No partial closes and no adds.
"""

from ftmo.tactics.noise_area import (  # noqa: F401
    CHECK_MINUTES, PAPER_MAX_EXPOSURE, PAPER_VOL_TARGET, SETTINGS, TUNABLE, decide, levels,
)


def trail(day: int, minute: int, position: int, lv) -> float:
    if position > 0:
        return max(lv.upper[day, minute], lv.vwap[day, minute])
    return min(lv.lower[day, minute], lv.vwap[day, minute])
