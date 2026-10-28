"""Binance USDⓈ-M futures client: our adapter around the official SDK.

The rest of the bot only talks to this class, never to the SDK directly, so the
SDK can be upgraded (its major version changes often) or swapped for CCXT here.

Like the spot wrapper, this is an allowlist: account reads, symbol settings,
user-data-stream keys. Order placement is added in the execution step.

Notes on the SDK (binance-sdk-derivatives-trading-usds-futures):
- It retries only GET/DELETE requests on server errors, never POST (new orders),
  so it cannot duplicate an order by retrying.
- Its default timeout is 1 s, too short for a home connection; we use 10 s.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from binance_common.configuration import ConfigurationRestAPI
from binance_common.constants import (DERIVATIVES_TRADING_USDS_FUTURES_REST_API_DEMO_URL,
                                      DERIVATIVES_TRADING_USDS_FUTURES_REST_API_PROD_URL)
# Note: the SDK's error classes (BadRequestError, ClientError, ServerError, ...) all
# inherit from Error directly - BadRequestError is NOT a ClientError - so catch Error.
from binance_common.errors import Error as BinanceApiError
from binance_sdk_derivatives_trading_usds_futures.derivatives_trading_usds_futures import \
    DerivativesTradingUsdsFutures
from binance_sdk_derivatives_trading_usds_futures.rest_api.models import ChangeMarginTypeMarginTypeEnum

from cryptobot.config import MAX_LEVERAGE_HARD_CAP, Credentials
from cryptobot.exchange.filters import SymbolRules, to_decimal

log = logging.getLogger(__name__)

REST_URLS = {
    "demo": DERIVATIVES_TRADING_USDS_FUTURES_REST_API_DEMO_URL,   # https://demo-fapi.binance.com
    "live": DERIVATIVES_TRADING_USDS_FUTURES_REST_API_PROD_URL,   # https://fapi.binance.com
}
# Market + user data streams. Since 2026-04-23 Binance routes streams by path:
#   <base>/market/...   klines, mark price, aggTrades, tickers
#   <base>/public/...   book ticker, depth
#   <base>/private/...  user data (listenKey)
# The old <base>/ws and <base>/stream paths no longer deliver market data on live.
WS_URLS = {
    "demo": "wss://demo-fstream.binance.com",
    "live": "wss://fstream.binance.com",
}

# Binance error codes we handle on purpose.
NO_NEED_TO_CHANGE_MARGIN_TYPE = -4046
LISTEN_KEY_NOT_FOUND = -1125


class FuturesSetupError(RuntimeError):
    """The account is not in the state the bot requires. The bot must not trade."""


@dataclass(frozen=True)
class FuturesBalance:
    asset: str
    balance: Decimal            # wallet balance
    available: Decimal          # can be used for new positions
    unrealized_pnl: Decimal


@dataclass(frozen=True)
class FuturesPosition:
    symbol: str
    amount: Decimal             # >0 long, <0 short (one-way mode)
    entry_price: Decimal
    mark_price: Decimal
    liquidation_price: Decimal
    unrealized_pnl: Decimal
    margin_type: str
    leverage: int | None


@dataclass(frozen=True)
class SymbolSetup:
    symbol: str
    leverage: int
    max_notional_at_leverage: Decimal
    margin_type: str


def api_error_code(error: Exception) -> int | None:
    """Binance's numeric error code (e.g. -4046) from an SDK exception, if any."""
    return getattr(error, "status_code", None)


def _dump(obj):
    """SDK responses are pydantic models; turn them into plain dicts/lists with Binance field names."""
    if isinstance(obj, list):
        return [_dump(x) for x in obj]
    return obj.model_dump(by_alias=True, exclude_none=True) if hasattr(obj, "model_dump") else obj


class FuturesClient:
    def __init__(self, environment: str, credentials: Credentials, timeout_ms: int = 10_000):
        if environment not in REST_URLS:
            raise ValueError(f"FuturesClient supports {tuple(REST_URLS)}, got {environment!r}")
        self.environment = environment
        self.ws_url = WS_URLS[environment]
        config = ConfigurationRestAPI(
            api_key=credentials.api_key,
            api_secret=credentials.api_secret,
            private_key=credentials.private_key,
            private_key_passphrase=credentials.private_key_passphrase,
            base_path=REST_URLS[environment],
            timeout=timeout_ms,
        )
        # Kept private: the rest of the bot must use the methods below.
        self._api = DerivativesTradingUsdsFutures(config_rest_api=config).rest_api
        logging.getLogger("ConnectorLogger").setLevel(logging.WARNING)  # the SDK resets this on init
        self.rules: dict[str, SymbolRules] = {}

    # ---- startup routine -----------------------------------------------------

    def startup(self, symbols: list[str], leverage: int) -> list[SymbolSetup]:
        """The full startup routine. Any API failure becomes a FuturesSetupError."""
        try:
            self.check_account_modes()
            setups = [self.configure_symbol(s, leverage) for s in symbols]
            self.load_rules(symbols)
        except BinanceApiError as e:
            raise FuturesSetupError(f"Binance API error during startup: {e}") from e
        return setups

    def check_account_modes(self) -> None:
        """One-way position mode and single-asset mode are required (see reference doc)."""
        if self._api.get_current_position_mode().data().dual_side_position:
            raise FuturesSetupError(
                "Account is in HEDGE position mode. Switch to One-way: Futures > Settings > Position Mode."
            )
        if self._api.get_current_multi_assets_mode().data().multi_assets_margin:
            raise FuturesSetupError(
                "Account is in Multi-Assets mode. Switch to Single-Asset: Futures > Settings > Asset Mode."
            )
        log.info("Account modes OK: one-way positions, single-asset margin")

    def configure_symbol(self, symbol: str, leverage: int) -> SymbolSetup:
        """Set ISOLATED margin and the leverage for one symbol. Safe to repeat."""
        if not 1 <= leverage <= MAX_LEVERAGE_HARD_CAP:
            raise FuturesSetupError(f"leverage {leverage} exceeds hard cap {MAX_LEVERAGE_HARD_CAP}")
        try:
            self._api.change_margin_type(symbol, ChangeMarginTypeMarginTypeEnum.ISOLATED)
        except BinanceApiError as e:
            if api_error_code(e) != NO_NEED_TO_CHANGE_MARGIN_TYPE:  # -4046 = already ISOLATED
                raise FuturesSetupError(f"{symbol}: could not set ISOLATED margin: {e}") from e
        try:
            resp = self._api.change_initial_leverage(symbol, leverage).data()
        except BinanceApiError as e:
            raise FuturesSetupError(f"{symbol}: could not set leverage {leverage}x: {e}") from e
        if int(resp.leverage) != leverage:
            raise FuturesSetupError(f"{symbol}: Binance set leverage {resp.leverage}, expected {leverage}")
        setup = SymbolSetup(symbol, int(resp.leverage), to_decimal(resp.max_notional_value), "ISOLATED")
        log.info("%s: ISOLATED, leverage %dx (max position at this leverage: %s USDT)",
                 symbol, setup.leverage, setup.max_notional_at_leverage)
        return setup

    def load_rules(self, symbols: list[str]) -> dict[str, SymbolRules]:
        """Fetch exchangeInfo once and cache tick size, step size and min notional."""
        info = self._api.exchange_information().data()
        by_symbol = {s.symbol: _dump(s) for s in info.symbols}
        for symbol in symbols:
            if symbol not in by_symbol:
                raise FuturesSetupError(f"{symbol} is not a USDⓈ-M futures symbol on {self.environment}")
            if by_symbol[symbol].get("status") != "TRADING":
                raise FuturesSetupError(f"{symbol} status is {by_symbol[symbol].get('status')}, not TRADING")
            self.rules[symbol] = SymbolRules.from_exchange_info(by_symbol[symbol])
        return self.rules

    # ---- account reads -------------------------------------------------------

    def balances(self) -> dict[str, FuturesBalance]:
        result = {}
        for b in _dump(self._api.futures_account_balance_v3().data()):
            if to_decimal(b["balance"]) != 0:
                result[b["asset"]] = FuturesBalance(
                    asset=b["asset"],
                    balance=to_decimal(b["balance"]),
                    available=to_decimal(b["availableBalance"]),
                    unrealized_pnl=to_decimal(b.get("crossUnPnl", "0")),
                )
        return result

    def positions(self) -> list[FuturesPosition]:
        """Open positions only (position amount not zero)."""
        result = []
        for p in _dump(self._api.position_information_v3().data()):
            if to_decimal(p["positionAmt"]) != 0:
                result.append(FuturesPosition(
                    symbol=p["symbol"],
                    amount=to_decimal(p["positionAmt"]),
                    entry_price=to_decimal(p["entryPrice"]),
                    mark_price=to_decimal(p["markPrice"]),
                    liquidation_price=to_decimal(p["liquidationPrice"]),
                    unrealized_pnl=to_decimal(p["unRealizedProfit"]),
                    margin_type=str(p.get("marginType", "")).upper(),
                    leverage=int(p["leverage"]) if p.get("leverage") else None,
                ))
        return result

    def open_order_counts(self) -> tuple[int, int]:
        """(normal open orders, open algo/conditional orders) - for reconciliation."""
        normal = _dump(self._api.current_all_open_orders().data())
        algo = _dump(self._api.current_all_algo_open_orders().data())
        return len(normal), len(algo)

    # ---- user data stream keys -----------------------------------------------

    def create_listen_key(self) -> str:
        """Returns the account's listenKey (Binance returns the active one if it exists)."""
        return self._api.start_user_data_stream().data().listen_key

    def keepalive_listen_key(self) -> None:
        """Extend the listenKey by 60 minutes. Raises ClientError -1125 if it no longer exists."""
        self._api.keepalive_user_data_stream()

    def close_listen_key(self) -> None:
        self._api.close_user_data_stream()
