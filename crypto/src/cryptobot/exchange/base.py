"""The interface every exchange client implements (CCXT, paper broker, official SDK later).

Safety by design: this is a deliberately small allowlist. There is no generic
"call any endpoint" method, so code elsewhere in the bot cannot reach account
transfer or fund-movement endpoints even by mistake.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from cryptobot.models import Balance, OrderRequest


class ExchangeClient(ABC):
    """Read market data, read balances, and (later) place approved orders."""

    environment: str  # "paper" | "testnet" | "live"

    @abstractmethod
    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        """Candles as a DataFrame indexed by UTC open time: open, high, low, close, volume."""

    @abstractmethod
    def fetch_balances(self) -> dict[str, Balance]:
        """Non-zero balances, keyed by asset (e.g. 'USDT')."""

    @abstractmethod
    def market_rules(self, symbol: str) -> dict:
        """Precision and min order size/notional for a symbol (used before placing orders)."""

    def create_order(self, order: OrderRequest) -> dict:
        """Place an order. Implemented in a later session, behind the executor + risk manager."""
        raise NotImplementedError("Order placement is not implemented yet (planned for the execution session).")
