"""Shared data types that modules pass to each other.

Why this file exists: the strategy, risk manager, executor and storage all talk in
these small dataclasses instead of raw CCXT dictionaries. Only the `exchange/`
package knows about CCXT, so we can later swap CCXT for Binance's official SDK
without touching anything else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class Signal:
    """What a strategy wants to do. NOT an order yet - no quantity, no keys.

    stop_loss is mandatory for entries; the risk manager rejects entries without one.
    """

    symbol: str
    side: Side
    stop_loss: float | None = None
    take_profit: float | None = None
    reason: str = ""


@dataclass(frozen=True)
class OrderRequest:
    """A concrete order we would like to place. Must be approved by the RiskManager.

    is_exit=True means the order closes/reduces an existing position. Exits reduce
    risk, so the risk manager lets them through even when trading is halted.
    """

    symbol: str
    side: Side
    quantity: float          # in base currency, e.g. BTC
    price: float             # limit price, or expected fill price for market orders
    stop_loss: float | None = None
    order_type: str = "market"
    is_exit: bool = False
    strategy: str = ""

    @property
    def notional(self) -> float:
        """Order value in quote currency (USDT)."""
        return self.quantity * self.price


@dataclass
class Position:
    symbol: str
    side: Side
    quantity: float
    entry_price: float
    stop_loss: float | None = None


@dataclass
class PortfolioSnapshot:
    """Everything the risk manager needs to know about the account, at one moment."""

    quote_balance: float
    open_positions: dict[str, Position] = field(default_factory=dict)
    realized_pnl_today: float = 0.0     # closed trades since 00:00 UTC
    unrealized_pnl: float = 0.0         # open positions marked to market

    @property
    def pnl_today(self) -> float:
        return self.realized_pnl_today + self.unrealized_pnl


@dataclass(frozen=True)
class Balance:
    asset: str
    free: float
    used: float
    total: float


@dataclass(frozen=True)
class Fill:
    """One execution (trade) of an order. Recorded in SQLite for tax reporting."""

    order_id: str
    symbol: str
    side: Side
    quantity: float
    price: float
    fee: float
    fee_asset: str
    timestamp: datetime          # always UTC

    @property
    def quote_value(self) -> float:
        return self.quantity * self.price
