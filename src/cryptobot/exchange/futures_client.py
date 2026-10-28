"""Binance USDⓈ-M futures client: our adapter around the official SDK.

The rest of the bot only talks to this class, never to the SDK directly, so the
SDK can be upgraded (its major version changes often) or swapped for CCXT here.

Like the spot wrapper, this is an allowlist: account reads, symbol settings,
user-data-stream keys, and a few order types (market orders, close-position
stop-market algo orders, cancel). Callers must get every order approved by the
RiskManager first.

Notes on the SDK (binance-sdk-derivatives-trading-usds-futures):
- It retries only GET/DELETE requests on server errors, never POST (new orders),
  so it cannot duplicate an order by retrying.
- Its default timeout is 1 s, too short for a home connection; we use 10 s.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from decimal import Decimal

from binance_common.configuration import ConfigurationRestAPI
from binance_common.constants import (DERIVATIVES_TRADING_USDS_FUTURES_REST_API_DEMO_URL,
                                      DERIVATIVES_TRADING_USDS_FUTURES_REST_API_PROD_URL)
# Note: the SDK's error classes (BadRequestError, ClientError, ServerError, ...) all
# inherit from Error directly - BadRequestError is NOT a ClientError - so catch Error.
from binance_common.errors import BadRequestError
from binance_common.errors import Error as BinanceApiError
from binance_sdk_derivatives_trading_usds_futures.derivatives_trading_usds_futures import \
    DerivativesTradingUsdsFutures
from binance_sdk_derivatives_trading_usds_futures.rest_api.models import (
    ChangeMarginTypeMarginTypeEnum, NewAlgoOrderAlgoTypeEnum, NewAlgoOrderClosePositionEnum,
    NewAlgoOrderPriceProtectEnum, NewAlgoOrderSideEnum,
    NewAlgoOrderTypeEnum, NewAlgoOrderWorkingTypeEnum, NewOrderNewOrderRespTypeEnum, NewOrderReduceOnlyEnum,
    NewOrderSideEnum, NewOrderTypeEnum)

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
ORDER_DOES_NOT_EXIST = -2013


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
    # How often / how long to look an order up after an unclear send result (~8 s total).
    query_attempts = 5
    query_delay_s = 2.0

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
        self.leverage: dict[str, int] = {}

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
        self.leverage[symbol] = setup.leverage
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
                    # positionRisk v3 has no marginType/leverage fields: an isolated
                    # position has its own wallet; leverage is what startup() set.
                    margin_type=str(p.get("marginType") or
                                    ("ISOLATED" if to_decimal(p.get("isolatedWallet", "0")) != 0 else "CROSSED")).upper(),
                    leverage=int(p["leverage"]) if p.get("leverage") else self.leverage.get(p["symbol"]),
                ))
        return result

    def open_order_counts(self) -> tuple[int, int]:
        """(normal open orders, open algo/conditional orders) - for reconciliation."""
        normal = _dump(self._api.current_all_open_orders().data())
        algo = _dump(self._api.current_all_algo_open_orders().data())
        return len(normal), len(algo)

    def mark_price(self, symbol: str) -> Decimal:
        data = self._api.mark_price(symbol=symbol).data()
        item = getattr(data, "actual_instance", data)
        if isinstance(item, list):
            item = item[0]
        return to_decimal(_dump(item)["markPrice"])

    # ---- orders (callers: get RiskManager approval first) ---------------------

    def place_market_order(self, symbol: str, side: str, quantity: Decimal, client_order_id: str,
                           reduce_only: bool = False) -> dict:
        """Send a MARKET order and return Binance's view of it (status, executedQty, avgPrice).

        Idempotency: the caller saves client_order_id BEFORE calling. If the request
        fails in a way where the order MAY have been placed (timeout, HTTP 5xx), we do
        NOT resend - we look the order up by client_order_id. Only a clear rejection
        (HTTP 400, e.g. bad quantity) is raised as an error.
        """
        rules = self.rules.get(symbol)
        if rules is None:
            raise ValueError(f"No exchange rules loaded for {symbol} - run startup() first")
        problems = rules.check(quantity, self.mark_price(symbol), market=True)
        if reduce_only:
            # Closing orders may be below the minimum order value; Binance allows that.
            problems = [p for p in problems if "notional" not in p]
        if problems:
            raise ValueError(f"{symbol} {quantity}: {problems}")
        # Build every argument BEFORE the network call: a bug here must fail loudly,
        # not be mistaken for "the request may have reached Binance".
        params = dict(
            symbol=symbol,
            side=NewOrderSideEnum(side.upper()),
            type=NewOrderTypeEnum.MARKET,
            # Decimal, not float: the SDK writes Decimals exactly ("0.00001"),
            # floats can become "1e-05", which Binance rejects.
            quantity=to_decimal(quantity),
            reduce_only=NewOrderReduceOnlyEnum("true" if reduce_only else "false"),
            new_client_order_id=client_order_id,
            new_order_resp_type=NewOrderNewOrderRespTypeEnum.RESULT,
        )
        try:
            self._api.new_order(**params)
        except BadRequestError:
            raise  # HTTP 400 = definite rejection: nothing was placed
        except BinanceApiError as e:
            log.warning("Order %s: unclear result (%s) - querying instead of resending", client_order_id, e)
        except Exception as e:  # noqa: BLE001 - network timeout etc.: the order may exist
            log.warning("Order %s: request failed (%s: %s) - querying instead of resending",
                        client_order_id, type(e).__name__, e)
        # The order may reach the matching engine a moment after a timeout, so one
        # "not found" is not proof. Ask a few times before concluding it was not placed.
        for attempt in range(self.query_attempts):
            order = self.query_order(symbol, client_order_id)
            if order is not None:
                return order
            if attempt < self.query_attempts - 1:
                time.sleep(self.query_delay_s)
        raise RuntimeError(f"Order {client_order_id} was not placed (not found on Binance after "
                           f"{self.query_attempts} checks)")

    def query_order(self, symbol: str, client_order_id: str) -> dict | None:
        try:
            return _dump(self._api.query_order(symbol=symbol, orig_client_order_id=client_order_id).data())
        except BinanceApiError as e:
            if api_error_code(e) == ORDER_DOES_NOT_EXIST:
                return None
            raise

    def place_stop_market_close(self, symbol: str, side: str, trigger_price: Decimal, client_algo_id: str) -> dict:
        """Exchange-side stop-loss that closes the WHOLE position (Algo Order API).

        closePosition=true: no quantity needed, can only reduce the position.
        workingType=MARK_PRICE: triggered by the mark price, which is harder to spike
        than the last traded price, so fewer false stop-outs.
        """
        resp = self._api.new_algo_order(
            algo_type=NewAlgoOrderAlgoTypeEnum.CONDITIONAL,
            symbol=symbol,
            side=NewAlgoOrderSideEnum(side.upper()),
            type=NewAlgoOrderTypeEnum.STOP_MARKET,
            trigger_price=to_decimal(trigger_price),
            close_position=NewAlgoOrderClosePositionEnum("true"),
            working_type=NewAlgoOrderWorkingTypeEnum.MARK_PRICE,
            # priceProtect: Binance won't trigger the stop if mark and last price have
            # drifted unusually far apart (protects against bad-tick triggers).
            price_protect=NewAlgoOrderPriceProtectEnum("true"),
            client_algo_id=client_algo_id,
        )
        return _dump(resp.data())

    def open_algo_orders(self, symbol: str) -> list[dict]:
        return _dump(self._api.current_all_algo_open_orders(symbol=symbol).data())

    def cancel_algo_order(self, client_algo_id: str) -> dict:
        return _dump(self._api.cancel_algo_order(client_algo_id=client_algo_id).data())

    # ---- user data stream keys -----------------------------------------------

    def create_listen_key(self) -> str:
        """Returns the account's listenKey (Binance returns the active one if it exists)."""
        return self._api.start_user_data_stream().data().listen_key

    def keepalive_listen_key(self) -> None:
        """Extend the listenKey by 60 minutes. Raises ClientError -1125 if it no longer exists."""
        self._api.keepalive_user_data_stream()

    def close_listen_key(self) -> None:
        self._api.close_user_data_stream()
