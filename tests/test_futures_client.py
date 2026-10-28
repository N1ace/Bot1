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


def test_startup_wraps_api_errors():
    api = FakeApi(margin_error=ServerError("Service Unavailable", 503))
    with pytest.raises(FuturesSetupError):
        client_with(api).startup(["BTCUSDT"], 2)
