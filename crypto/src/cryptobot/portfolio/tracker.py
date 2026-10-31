"""Keeps track of balances, open positions and today's PnL.

Skeleton for now: it builds the PortfolioSnapshot the risk manager needs.
Position tracking from fills and PnL marking come with the execution session.
"""

from __future__ import annotations

from cryptobot.exchange.base import ExchangeClient
from cryptobot.models import PortfolioSnapshot, Position


class PortfolioTracker:
    def __init__(self, client: ExchangeClient, quote_currency: str = "USDT"):
        self.client = client
        self.quote_currency = quote_currency
        self.positions: dict[str, Position] = {}
        self.realized_pnl_today = 0.0

    def snapshot(self) -> PortfolioSnapshot:
        balances = self.client.fetch_balances()
        quote = balances.get(self.quote_currency)
        return PortfolioSnapshot(
            quote_balance=quote.free if quote else 0.0,
            open_positions=dict(self.positions),
            realized_pnl_today=self.realized_pnl_today,
            unrealized_pnl=0.0,  # TODO: mark open positions to the latest price
        )
