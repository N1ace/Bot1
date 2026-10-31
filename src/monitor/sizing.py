"""Layer 0: gap-aware sizing, for a trading bot to call when it sizes a position.

In a crash a stop can fill far past its price. So assume a fill at 2.5x the stop distance
and cap the position so that even then it loses at most 1.5% of equity. Uses Decimal, like
every real order quantity, and rounds down to the exchange's step. The result still goes
through the bot's risk check before any order.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal


def gap_capped_qty(qty: Decimal, equity: Decimal, stop_distance: Decimal, step: Decimal,
                   stop_fill_multiple: Decimal = Decimal("2.5"), max_gap_loss: Decimal = Decimal("0.015")) -> Decimal:
    """The same qty or a smaller one, signed like qty, never larger. 0 when the inputs make no sense."""
    values = (qty, equity, stop_distance, step, stop_fill_multiple, max_gap_loss)
    if not all(isinstance(v, Decimal) and v.is_finite() for v in values):
        raise TypeError("gap_capped_qty needs finite Decimal values")
    if equity <= 0 or stop_distance <= 0 or step <= 0 or stop_fill_multiple <= 0 or max_gap_loss <= 0:
        return Decimal(0)
    limit = equity * max_gap_loss / (stop_distance * stop_fill_multiple)
    capped = min(abs(qty), limit)
    rounded = (capped / step).to_integral_value(rounding=ROUND_DOWN) * step
    return rounded if qty >= 0 else -rounded
