"""Paper trading: real public market data, simulated balances and (later) simulated fills.

Why it matters: Binance has a spot testnet, but NO testnet for spot margin or
copy trading. The paper broker is how those modes get tested safely, and the
backtester will reuse it.
"""

from __future__ import annotations

import pandas as pd

from cryptobot.exchange.base import ExchangeClient
from cryptobot.exchange.market_data import BinancePublicData
from cryptobot.models import Balance


class PaperExchangeClient(ExchangeClient):
    environment = "paper"

    def __init__(self, starting_balances: dict[str, float], market_data: BinancePublicData | None = None):
        # Prices come from the real market (read-only, no keys).
        self._data = market_data or BinancePublicData()
        self._balances = dict(starting_balances)

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        return self._data.fetch_ohlcv(symbol, timeframe, limit)

    def fetch_balances(self) -> dict[str, Balance]:
        return {a: Balance(asset=a, free=v, used=0.0, total=v) for a, v in self._balances.items() if v}

    def market_rules(self, symbol: str) -> dict:
        return self._data.market_rules(symbol)

    # create_order: simulated fills (with fees and slippage) come in the execution session.
