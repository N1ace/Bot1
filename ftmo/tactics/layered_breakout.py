"""Layered noise-area breakout, tested as a declared exception approved by the owner on 2026-11-01.

HYPOTHESIS: a break out of the day's normal range continues into the close, and no one band width is special. Splitting
the position across several widths should keep the edge while removing the dependence on a single number.

This idea came from noise_area's failure on this same data, so its results carry extra doubt, even if it passes. It counts
as a new tactic. Rules: the entries, VWAP stop, hard stop and close-out are noise_area's. The four band widths cover the
paper's two tested values, 1 and 1.5, in equal steps. They weren't chosen from test results.
"""

from ftmo.tactics.noise_area import CHECK_MINUTES, PAPER_MAX_EXPOSURE, PAPER_VOL_TARGET, decide, levels  # noqa: F401

SETTINGS = {"lookback_days": 14, "band_multiplier": 1.0, "hard_stop_multiple": 2.0}
TUNABLE = ("lookback_days", "band_multiplier")  # moving band_multiplier moves all four layers together
LAYERS = (1.0, 1.17, 1.33, 1.5)


def layer_settings(settings: dict, layer: float) -> dict:
    return dict(settings, band_multiplier=settings["band_multiplier"] * layer)
