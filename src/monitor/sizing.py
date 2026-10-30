"""Layer 0: gap-aware sizing, for a trading bot to call when it sizes a position.

In a crash a stop can fill far past its price. So assume a fill at 2.5x the stop distance
and cap the position so that even then it loses at most 1.5% of equity.
"""

from __future__ import annotations

import math


def gap_capped_qty(qty: float, equity: float, stop_distance: float, stop_fill_multiple: float = 2.5,
                   max_gap_loss: float = 0.015) -> float:
    """The same qty, or a smaller one. Never larger. 0 when the inputs make no sense."""
    if not all(map(math.isfinite, (qty, equity, stop_distance))) or equity <= 0 or stop_distance <= 0:
        return 0.0
    limit = equity * max_gap_loss / (stop_distance * stop_fill_multiple)
    return math.copysign(min(abs(qty), limit), qty)
