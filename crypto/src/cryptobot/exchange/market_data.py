"""Real Binance market data from the public, market-data-only server.

https://data-api.binance.vision serves the same public data as api.binance.com
(candles, prices, exchange rules) but cannot accept orders or account requests
at all, and needs no API key. So using it for prices is safe even while orders
go to the testnet.

Why real data with testnet orders: testnet order books are separate from the
real market and very thin, so testnet candles have random spikes. Strategies
should look at real prices. Note the catch: testnet orders still fill at TESTNET
prices, which can differ from the real price by 1-2% or more.
"""

from __future__ import annotations

import ccxt
import pandas as pd

from cryptobot.exchange.base import ExchangeClient
from cryptobot.exchange.ccxt_client import market_rules_from, ohlcv_to_dataframe
from cryptobot.models import Balance

DATA_API_URL = "https://data-api.binance.vision/api/v3"


class BinancePublicData(ExchangeClient):
    environment = "public"

    def __init__(self):
        # No keys on purpose: this client can only read public data.
        self._ex = ccxt.binance(
            {"enableRateLimit": True, "options": {"defaultType": "spot", "fetchMarkets": {"types": ["spot"]}}}
        )
        self._ex.urls["api"]["public"] = DATA_API_URL
        self._ex.load_markets()

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        return ohlcv_to_dataframe(self._ex.fetch_ohlcv(symbol, timeframe, limit=limit))

    def fetch_balances(self) -> dict[str, Balance]:
        raise RuntimeError("Public market data has no account balances")

    def market_rules(self, symbol: str) -> dict:
        return market_rules_from(self._ex.market(symbol))
