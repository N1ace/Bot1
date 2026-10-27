"""Binance SPOT client built on CCXT.

Margin and futures clients will be separate classes later (different endpoints,
different rules), each created with its own API key.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, TypeVar

import ccxt
import pandas as pd

from cryptobot.config import Credentials
from cryptobot.exchange.base import ExchangeClient
from cryptobot.models import Balance

log = logging.getLogger(__name__)
T = TypeVar("T")


class CcxtSpotClient(ExchangeClient):
    def __init__(self, environment: str, credentials: Credentials | None = None):
        if environment not in ("testnet", "live"):
            raise ValueError("CcxtSpotClient is for 'testnet' or 'live'; paper mode uses PaperExchangeClient")
        self.environment = environment
        self.has_credentials = credentials is not None

        # Kept private (leading underscore): the rest of the bot must go through the
        # methods below, never through the raw CCXT object.
        self._ex = ccxt.binance(
            {
                "apiKey": credentials.api_key if credentials else None,
                "secret": credentials.api_secret if credentials else None,
                # CCXT waits between requests so we stay under Binance's rate limits.
                "enableRateLimit": True,
                "options": {
                    "defaultType": "spot",
                    # Only load spot markets. Futures endpoints are not needed here
                    # and their old testnet is no longer supported by CCXT.
                    "fetchMarkets": {"types": ["spot"]},
                    # Fixes "timestamp outside recvWindow" errors if the PC clock drifts.
                    "adjustForTimeDifference": True,
                },
            }
        )
        if environment == "testnet":
            # Points CCXT at testnet.binance.vision instead of the real exchange.
            self._ex.set_sandbox_mode(True)

        self._call(self._ex.load_markets)
        log.info("Connected to Binance spot %s (%d markets)", environment, len(self._ex.markets))

    # ---- read-only calls (safe to retry) -----------------------------------

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        rows = self._call(lambda: self._ex.fetch_ohlcv(symbol, timeframe, limit=limit))
        return ohlcv_to_dataframe(rows)

    def fetch_balances(self) -> dict[str, Balance]:
        if not self.has_credentials:
            raise RuntimeError("No API keys configured - cannot read balances")
        raw = self._call(self._ex.fetch_balance)
        balances = {}
        for asset, total in (raw.get("total") or {}).items():
            if total:  # skip zero / None balances
                balances[asset] = Balance(
                    asset=asset,
                    free=float(raw["free"].get(asset) or 0),
                    used=float(raw["used"].get(asset) or 0),
                    total=float(total),
                )
        return balances

    def market_rules(self, symbol: str) -> dict:
        return market_rules_from(self._ex.market(symbol))

    # ---- helpers -----------------------------------------------------------

    def _call(self, fn: Callable[[], T], attempts: int = 3) -> T:
        """Retry READ-ONLY calls on temporary network problems.

        Order placement must NOT use this: if a "create order" request times out,
        the order may still have been placed, and a blind retry could double it.
        """
        for attempt in range(1, attempts + 1):
            try:
                return fn()
            except (ccxt.NetworkError, ccxt.RateLimitExceeded) as e:
                if attempt == attempts:
                    raise
                wait = 2**attempt
                log.warning("Network problem (%s), retry %d/%d in %ds", type(e).__name__, attempt, attempts - 1, wait)
                time.sleep(wait)
        raise AssertionError("unreachable")


def market_rules_from(market: dict) -> dict:
    """Pick the trading rules we care about out of a CCXT market description."""
    return {
        "amount_precision": market["precision"]["amount"],  # quantity step, e.g. 0.00001 BTC
        "price_precision": market["precision"]["price"],    # price tick, e.g. 0.01 USDT
        "min_amount": market["limits"]["amount"]["min"],
        "min_notional": market["limits"]["cost"]["min"],    # min order value, e.g. 5 USDT
    }


def ohlcv_to_dataframe(rows: list[list]) -> pd.DataFrame:
    """CCXT gives [[timestamp_ms, open, high, low, close, volume], ...]; make it a DataFrame."""
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.set_index("timestamp").astype(float)
