"""Exchange rules and Decimal rounding. Rule values are the real BTCUSDT demo filters."""

from decimal import Decimal

import pytest

from cryptobot.exchange.filters import SymbolRules, to_decimal

BTC_INFO = {
    "symbol": "BTCUSDT",
    "filters": [
        {"filterType": "PRICE_FILTER", "maxPrice": "809484", "minPrice": "261.10", "tickSize": "0.10"},
        {"filterType": "LOT_SIZE", "maxQty": "1000", "minQty": "0.0001", "stepSize": "0.0001"},
        {"filterType": "MARKET_LOT_SIZE", "maxQty": "120", "minQty": "0.0001", "stepSize": "0.0001"},
        {"filterType": "MIN_NOTIONAL", "notional": "50"},
        {"filterType": "PERCENT_PRICE", "multiplierUp": "1.0500", "multiplierDown": "0.9500"},
    ],
}


@pytest.fixture
def btc():
    return SymbolRules.from_exchange_info(BTC_INFO)


def test_parses_filters(btc):
    assert btc.tick_size == Decimal("0.10")
    assert btc.step_size == Decimal("0.0001")
    assert btc.market_max_qty == Decimal("120")
    assert btc.min_notional == Decimal("50")


def test_to_decimal_avoids_float_noise():
    assert to_decimal(0.1) == Decimal("0.1")
    assert to_decimal(0.1) + to_decimal(0.2) == Decimal("0.3")


@pytest.mark.parametrize("direction,expected", [("down", "79999.10"), ("up", "79999.20"), ("nearest", "79999.20")])
def test_round_price(btc, direction, expected):
    assert btc.round_price(79999.17, direction) == Decimal(expected)


def test_round_price_keeps_exact_values(btc):
    assert btc.round_price("80000.30", "up") == Decimal("80000.30")


def test_round_qty_always_down(btc):
    assert btc.round_qty("0.00129") == Decimal("0.0012")
    assert btc.round_qty("0.00121", market=True) == Decimal("0.0012")


def test_check_accepts_valid_order(btc):
    assert btc.check("0.0010", "80000.0") == []


def test_check_reports_all_problems(btc):
    problems = btc.check("0.00005", "80000.05")
    text = " ".join(problems)
    assert "step" in text and "below minimum" in text and "tick" in text and "notional" in text


def test_check_market_uses_market_limits(btc):
    assert any("above maximum" in p for p in btc.check("121", "80000", market=True))
    assert btc.check("121", "80000.0", market=False) == []


def test_min_qty_for_notional(btc):
    qty = btc.min_qty_for_notional("80000")   # 50 / 80000 = 0.000625 -> rounds UP to 0.0007
    assert qty == Decimal("0.0007")
    assert btc.check(qty, "80000", market=True) == []
