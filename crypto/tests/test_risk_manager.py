"""Tests for the risk manager. No network, no exchange - just logic."""

from datetime import date

import pytest

from cryptobot.config import RiskConfig
from cryptobot.models import OrderRequest, PortfolioSnapshot, Position, Side
from cryptobot.risk import RiskManager

LIMITS = RiskConfig(max_order_value=50, max_open_positions=2, max_daily_loss=30, require_stop_loss=True)


class FakeClock:
    """Lets tests control what 'today' is."""

    def __init__(self, day: date):
        self.day = day

    def __call__(self) -> date:
        return self.day


@pytest.fixture
def clock():
    return FakeClock(date(2026, 1, 1))


@pytest.fixture
def rm(clock):
    return RiskManager(LIMITS, today=clock)


def buy(symbol="BTC/USDT", qty=0.0004, price=100_000, stop=98_000, **kw):
    return OrderRequest(symbol=symbol, side=Side.BUY, quantity=qty, price=price, stop_loss=stop, **kw)


def snap(balance=1000, positions=None, realized=0.0, unrealized=0.0):
    return PortfolioSnapshot(quote_balance=balance, open_positions=positions or {},
                             realized_pnl_today=realized, unrealized_pnl=unrealized)


def btc_position():
    return {"BTC/USDT": Position("BTC/USDT", Side.BUY, 0.0004, 100_000, 98_000)}


def test_valid_entry_is_approved(rm):
    decision = rm.review(buy(), snap())  # 0.0004 * 100k = 40 USDT <= 50
    assert decision.approved
    assert decision.reasons == []


def test_rejects_order_above_max_order_value(rm):
    decision = rm.review(buy(qty=0.001), snap())  # 100 USDT > 50
    assert not decision.approved
    assert "max_order_value" in decision.reasons[0]


def test_rejects_when_balance_too_low(rm):
    assert not rm.review(buy(), snap(balance=10)).approved


def test_rejects_missing_stop_loss(rm):
    decision = rm.review(buy(stop=None), snap())
    assert not decision.approved
    assert "stop-loss is mandatory" in decision.reasons[0]


@pytest.mark.parametrize("stop", [100_000, 101_000])
def test_rejects_buy_stop_loss_at_or_above_entry(rm, stop):
    assert not rm.review(buy(stop=stop), snap()).approved


def test_short_entry_needs_stop_above_price(rm):
    short = OrderRequest("ETH/USDT", Side.SELL, 0.01, 3000, stop_loss=2900)
    assert not rm.review(short, snap()).approved
    assert rm.review(OrderRequest("ETH/USDT", Side.SELL, 0.01, 3000, stop_loss=3100), snap()).approved


def test_rejects_when_max_open_positions_reached(rm):
    positions = {
        "BTC/USDT": Position("BTC/USDT", Side.BUY, 0.0004, 100_000),
        "ETH/USDT": Position("ETH/USDT", Side.BUY, 0.01, 3000),
    }
    decision = rm.review(buy(symbol="SOL/USDT", qty=0.2, price=200, stop=190), snap(positions=positions))
    assert not decision.approved
    assert "max_open_positions" in decision.reasons[0]


def test_rejects_second_entry_in_same_symbol(rm):
    assert not rm.review(buy(), snap(positions=btc_position())).approved


def test_collects_all_reasons(rm):
    decision = rm.review(buy(qty=0.001, stop=None), snap())
    assert len(decision.reasons) == 2  # too big AND no stop-loss


def test_daily_loss_limit_halts_trading(rm):
    calls = []
    rm._on_halt = calls.append
    decision = rm.review(buy(), snap(realized=-20, unrealized=-10))  # -30 hits the limit
    assert not decision.approved
    assert rm.is_halted
    assert len(calls) == 1
    # Even after the loss "recovers", entries stay blocked today.
    assert not rm.review(buy(), snap()).approved


def test_halt_resets_next_utc_day(rm, clock):
    rm.halt("test")
    assert rm.is_halted
    clock.day = date(2026, 1, 2)
    assert not rm.is_halted
    assert rm.review(buy(), snap()).approved


def test_halt_from_previous_run_is_respected(clock):
    rm = RiskManager(LIMITS, today=clock, halted_on=clock.day)  # loaded from SQLite at startup
    assert not rm.review(buy(), snap()).approved


def test_exit_allowed_while_halted(rm):
    rm.halt("test")
    exit_order = OrderRequest("BTC/USDT", Side.SELL, 0.0004, 99_000, is_exit=True)
    assert rm.review(exit_order, snap(positions=btc_position())).approved


def test_exit_rejected_without_position_or_too_large(rm):
    assert not rm.review(OrderRequest("BTC/USDT", Side.SELL, 0.0004, 99_000, is_exit=True), snap()).approved
    too_big = OrderRequest("BTC/USDT", Side.SELL, 0.001, 99_000, is_exit=True)
    assert not rm.review(too_big, snap(positions=btc_position())).approved


@pytest.mark.parametrize("qty,price", [(0, 100), (-1, 100), (1, 0)])
def test_rejects_non_positive_quantity_or_price(rm, qty, price):
    assert not rm.review(buy(qty=qty, price=price), snap()).approved
