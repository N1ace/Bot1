"""Paper trading: real public market data, simulated balances and (later) simulated fills.

Why it matters: Binance has a spot testnet, but NO testnet for spot margin or
copy trading. The paper broker is how those modes get tested safely, and the
backtester will reuse it.
"""

from __future__ import annotations

import ccxt
import pandas as pd

from cryptobot.exchange.base import ExchangeClient
from cryptobot.exchange.ccxt_client import market_rules_from, ohlcv_to_dataframe
from cryptobot.models import Balance


class PaperExchangeClient(ExchangeClient):
    environment = "paper"

    def __init__(self, starting_balances: dict[str, float]):
        # Public client with NO keys: it can read prices but cannot touch any account.
        self._public = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "spot", "fetchMarkets": {"types": ["spot"]}}})
        self._public.load_markets()
        self._balances = dict(starting_balances)

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        return ohlcv_to_dataframe(self._public.fetch_ohlcv(symbol, timeframe, limit=limit))

    def fetch_balances(self) -> dict[str, Balance]:
        return {a: Balance(asset=a, free=v, used=0.0, total=v) for a, v in self._balances.items() if v}

    def market_rules(self, symbol: str) -> dict:
        return market_rules_from(self._public.market(symbol))

    # create_order: simulated fills (with fees and slippage) come in the execution session.
