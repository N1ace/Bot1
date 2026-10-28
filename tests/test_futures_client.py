"""FuturesClient logic with a fake SDK (no network).

Regression test: the SDK raises BadRequestError for -4046 "No need to change
margin type", and BadRequestError is not a ClientError. The first real demo run
crashed on this, so it is tested here with the SDK's real exception classes.
"""

from types import SimpleNamespace

import pytest
from binance_common.errors import BadRequestError, ServerError

from cryptobot.exchange.futures_client import FuturesClient, FuturesSetupError


class FakeApi:
    def __init__(self, margin_error=None, dual_side=False, multi_assets=False):
        self.margin_error = margin_error
        self.dual_side = dual_side
        self.multi_assets = multi_assets
        self.leverage_calls = []

    @staticmethod
    def _resp(**fields):
        return SimpleNamespace(data=lambda: SimpleNamespace(**fields))

    def change_margin_type(self, symbol, margin_type):
        if self.margin_error:
            raise self.margin_error
        return self._resp()

    def change_initial_leverage(self, symbol, leverage):
        self.leverage_calls.append((symbol, leverage))
        return self._resp(leverage=leverage, max_notional_value="300000000", symbol=symbol)

    def get_current_position_mode(self):
        return self._resp(dual_side_position=self.dual_side)

    def get_current_multi_assets_mode(self):
        return self._resp(multi_assets_margin=self.multi_assets)


def client_with(api) -> FuturesClient:
    client = FuturesClient.__new__(FuturesClient)  # skip __init__: no SDK, no keys
    client._api, client.rules, client.environment = api, {}, "demo"
    return client


def test_already_isolated_is_fine():
    api = FakeApi(margin_error=BadRequestError("No need to change margin type.", -4046))
    setup = client_with(api).configure_symbol("BTCUSDT", 2)
    assert setup.leverage == 2 and setup.margin_type == "ISOLATED"
    assert api.leverage_calls == [("BTCUSDT", 2)]


def test_other_margin_errors_stop_startup():
    api = FakeApi(margin_error=BadRequestError("Margin type cannot be changed if there exists position.", -4048))
    with pytest.raises(FuturesSetupError, match="ISOLATED"):
        client_with(api).configure_symbol("BTCUSDT", 2)


def test_leverage_above_hard_cap_never_reaches_binance():
    api = FakeApi()
    with pytest.raises(FuturesSetupError, match="hard cap"):
        client_with(api).configure_symbol("BTCUSDT", 5)
    assert api.leverage_calls == []


def test_hedge_mode_is_refused():
    with pytest.raises(FuturesSetupError, match="One-way"):
        client_with(FakeApi(dual_side=True)).check_account_modes()


def test_multi_assets_mode_is_refused():
    with pytest.raises(FuturesSetupError, match="Single-Asset"):
        client_with(FakeApi(multi_assets=True)).check_account_modes()


# ---- orders ------------------------------------------------------------------

from decimal import Decimal

from cryptobot.exchange.filters import SymbolRules

BTC_RULES = SymbolRules.from_exchange_info({"symbol": "BTCUSDT", "filters": [
    {"filterType": "PRICE_FILTER", "maxPrice": "809484", "minPrice": "261.10", "tickSize": "0.10"},
    {"filterType": "LOT_SIZE", "maxQty": "1000", "minQty": "0.0001", "stepSize": "0.0001"},
    {"filterType": "MARKET_LOT_SIZE", "maxQty": "120", "minQty": "0.0001", "stepSize": "0.0001"},
    {"filterType": "MIN_NOTIONAL", "notional": "50"},
]})


class OrderApi(FakeApi):
    """new_order either succeeds, or raises `new_order_error`; query_order finds the order or not."""

    def __init__(self, new_order_error=None, order_exists=True):
        super().__init__()
        self.new_order_error, self.order_exists = new_order_error, order_exists
        self.new_order_calls = 0

    def mark_price(self, symbol):
        return SimpleNamespace(data=lambda: SimpleNamespace(actual_instance={"markPrice": "80000"}))

    def new_order(self, **kwargs):
        self.new_order_calls += 1
        self.last_params = kwargs
        if self.new_order_error:
            raise self.new_order_error
        return self._resp()

    def query_order(self, symbol, orig_client_order_id):
        self.query_calls = getattr(self, "query_calls", 0) + 1
        appears_on_query = getattr(self, "appears_on_query", 1)
        if not self.order_exists or self.query_calls < appears_on_query:
            raise BadRequestError("Order does not exist.", -2013)
        return SimpleNamespace(data=lambda: {"clientOrderId": orig_client_order_id, "status": "FILLED",
                                             "avgPrice": "80000", "executedQty": "0.0007", "orderId": 1})


def order_client(api):
    client = client_with(api)
    client.rules = {"BTCUSDT": BTC_RULES}
    client.query_delay_s = 0  # no real waiting in tests
    return client


def test_market_order_returns_queried_order():
    api = OrderApi()
    order = order_client(api).place_market_order("BTCUSDT", "BUY", Decimal("0.0007"), "cbtest-1")
    assert order["status"] == "FILLED" and api.new_order_calls == 1


def test_order_params_sent_to_sdk():
    from binance_common.utils import make_serializable
    api = OrderApi()
    order_client(api).place_market_order("BTCUSDT", "SELL", Decimal("0.0007"), "cbtest-p", reduce_only=True)
    p = api.last_params
    assert make_serializable(p["quantity"]) == "0.0007"           # exact text, never "7e-04"
    assert make_serializable(p["side"]) == "SELL" and make_serializable(p["type"]) == "MARKET"
    assert make_serializable(p["reduce_only"]) == "true"
    assert p["new_client_order_id"] == "cbtest-p"


def test_unclear_failure_is_queried_not_resent():
    """HTTP 503 'unknown status': the order may exist - look it up, never send it twice."""
    api = OrderApi(new_order_error=ServerError("Unknown error, please check your request", 503))
    order = order_client(api).place_market_order("BTCUSDT", "BUY", Decimal("0.0007"), "cbtest-2")
    assert order["clientOrderId"] == "cbtest-2" and api.new_order_calls == 1


def test_unclear_failure_and_not_found_raises():
    api = OrderApi(new_order_error=TimeoutError("read timeout"), order_exists=False)
    with pytest.raises(RuntimeError, match="not placed"):
        order_client(api).place_market_order("BTCUSDT", "BUY", Decimal("0.0007"), "cbtest-3")
    assert api.new_order_calls == 1 and api.query_calls == 5   # asked several times, never resent


def test_order_appearing_late_after_timeout_is_found():
    """Reviewer finding: the order may show up a moment after the timeout."""
    api = OrderApi(new_order_error=TimeoutError("read timeout"))
    api.appears_on_query = 3   # 'not found' twice, then found
    order = order_client(api).place_market_order("BTCUSDT", "BUY", Decimal("0.0007"), "cbtest-late")
    assert order["status"] == "FILLED" and api.new_order_calls == 1


def test_clear_rejection_is_raised_without_query():
    api = OrderApi(new_order_error=BadRequestError("Margin is insufficient.", -2019))
    with pytest.raises(BadRequestError):
        order_client(api).place_market_order("BTCUSDT", "BUY", Decimal("0.0007"), "cbtest-4")


def test_invalid_quantity_never_reaches_binance():
    api = OrderApi()
    with pytest.raises(ValueError, match="notional"):
        order_client(api).place_market_order("BTCUSDT", "BUY", Decimal("0.0001"), "cbtest-5")  # 8 USDT < 50
    assert api.new_order_calls == 0


def test_reduce_only_close_may_be_below_min_notional():
    api = OrderApi()
    order_client(api).place_market_order("BTCUSDT", "SELL", Decimal("0.0001"), "cbtest-6", reduce_only=True)
    assert api.new_order_calls == 1


def test_startup_wraps_api_errors():
    api = FakeApi(margin_error=ServerError("Service Unavailable", 503))
    with pytest.raises(FuturesSetupError):
        client_with(api).startup(["BTCUSDT"], 2)
