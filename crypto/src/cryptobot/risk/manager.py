"""The central risk manager. EVERY order must be approved here before it is sent.

Design decisions:
- It is "pure": it gets the order + a portfolio snapshot and returns a decision.
  No network calls, no database. That makes it fast and very easy to test.
- Entries (orders that open risk) get all checks. Exits (orders that close a
  position) are always allowed, even when trading is halted - you must always
  be able to get OUT of a trade.
- Hitting the daily loss limit halts new entries for the rest of the UTC day.
  The caller persists that in SQLite so restarting the bot does not reset it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Callable

from cryptobot.config import RiskConfig
from cryptobot.models import OrderRequest, PortfolioSnapshot, Side

log = logging.getLogger(__name__)


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    order: OrderRequest
    reasons: list[str] = field(default_factory=list)  # why it was rejected (empty if approved)


class RiskManager:
    def __init__(
        self,
        limits: RiskConfig,
        today: Callable[[], date] = utc_today,
        halted_on: date | None = None,
        on_halt: Callable[[str], None] | None = None,
    ):
        """
        limits     - risk settings from config.yaml
        today      - returns the current UTC date (tests pass a fake clock)
        halted_on  - date a previous run halted on (loaded from SQLite at startup)
        on_halt    - callback, e.g. save to SQLite + send a Telegram alert
        """
        self.limits = limits
        self._today = today
        self._halted_on = halted_on
        self._on_halt = on_halt
        self.halt_reason = ""

    # ---- kill switch -------------------------------------------------------

    @property
    def is_halted(self) -> bool:
        """True while today's trading is stopped. Resets automatically at 00:00 UTC."""
        return self._halted_on == self._today()

    def halt(self, reason: str) -> None:
        if self.is_halted:
            return
        self._halted_on = self._today()
        self.halt_reason = reason
        log.error("TRADING HALTED for today: %s", reason)
        if self._on_halt:
            self._on_halt(reason)

    # ---- the main check ----------------------------------------------------

    def review(self, order: OrderRequest, snapshot: PortfolioSnapshot) -> RiskDecision:
        """Approve or reject one order. Returns ALL reasons for a rejection, not just the first."""
        if order.quantity <= 0 or order.price <= 0:
            return self._reject(order, ["quantity and price must be positive"])

        if order.is_exit:
            return self._review_exit(order, snapshot)

        # Entries ------------------------------------------------------------
        if self.is_halted:
            return self._reject(order, [f"trading halted for today: {self.halt_reason or 'daily loss limit'}"])

        if snapshot.pnl_today <= -self.limits.max_daily_loss:
            self.halt(f"daily loss {snapshot.pnl_today:.2f} reached limit -{self.limits.max_daily_loss:.2f}")
            return self._reject(order, ["max daily loss reached"])

        reasons: list[str] = []
        lim = self.limits

        if order.notional > lim.max_order_value:
            reasons.append(f"order value {order.notional:.2f} > max_order_value {lim.max_order_value:.2f}")

        if order.notional > snapshot.quote_balance:
            reasons.append(f"order value {order.notional:.2f} > available balance {snapshot.quote_balance:.2f}")

        if order.symbol in snapshot.open_positions:
            # Keeping it simple: one position per symbol, no adding to winners/losers.
            reasons.append(f"already have an open position in {order.symbol}")
        elif len(snapshot.open_positions) >= lim.max_open_positions:
            reasons.append(f"max_open_positions ({lim.max_open_positions}) reached")

        reasons += self._check_stop_loss(order)

        return self._reject(order, reasons) if reasons else self._approve(order)

    # ---- helpers -----------------------------------------------------------

    def _review_exit(self, order: OrderRequest, snapshot: PortfolioSnapshot) -> RiskDecision:
        pos = snapshot.open_positions.get(order.symbol)
        if pos is None:
            return self._reject(order, [f"exit order but no open position in {order.symbol}"])
        if order.side == pos.side:
            return self._reject(order, ["exit order must be on the opposite side of the position"])
        if order.quantity > pos.quantity:
            return self._reject(order, [f"exit quantity {order.quantity} > position size {pos.quantity}"])
        return self._approve(order)

    def _check_stop_loss(self, order: OrderRequest) -> list[str]:
        if not self.limits.require_stop_loss:
            return []
        if order.stop_loss is None:
            return ["stop-loss is mandatory for entries"]
        # A long's stop must be BELOW entry; a short's (margin, later) must be ABOVE.
        if order.side == Side.BUY and order.stop_loss >= order.price:
            return [f"buy stop-loss {order.stop_loss} must be below entry price {order.price}"]
        if order.side == Side.SELL and order.stop_loss <= order.price:
            return [f"sell stop-loss {order.stop_loss} must be above entry price {order.price}"]
        return []

    def _approve(self, order: OrderRequest) -> RiskDecision:
        log.info("Risk APPROVED %s %s %.8f @ %.8f", order.side.value, order.symbol, order.quantity, order.price)
        return RiskDecision(approved=True, order=order)

    def _reject(self, order: OrderRequest, reasons: list[str]) -> RiskDecision:
        log.warning("Risk REJECTED %s %s: %s", order.side.value, order.symbol, "; ".join(reasons))
        return RiskDecision(approved=False, order=order, reasons=reasons)
