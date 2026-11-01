from datetime import date, datetime
from types import SimpleNamespace

import pandas as pd
import pytest

import ftmo.bot as bot
from ftmo.backtest import build_days


def account(mode=bot.mt5.ACCOUNT_TRADE_MODE_DEMO, server="FTMO-Demo", login=111):
    return SimpleNamespace(trade_mode=mode, server=server, login=login)


def test_refuses_a_real_account():
    with pytest.raises(SystemExit):
        bot.check_account(account(bot.mt5.ACCOUNT_TRADE_MODE_REAL), False, None)


def test_refuses_a_server_not_named_demo():
    with pytest.raises(SystemExit):
        bot.check_account(account(server="FTMO-Server"), False, None)


def test_order_mode_must_name_the_exact_login():
    bot.check_account(account(), False, None)
    bot.check_account(account(), True, 111)
    with pytest.raises(SystemExit):
        bot.check_account(account(), True, 222)
    with pytest.raises(SystemExit):
        bot.check_account(account(), True, None)


def test_server_clock_becomes_new_york_minutes():
    assert bot.session_clock(datetime(2026, 11, 2, 16, 30)) == (date(2026, 11, 2), 0)
    assert bot.session_clock(datetime(2026, 11, 2, 22, 59)) == (date(2026, 11, 2), 389)


def test_fomc_break_covers_the_statement():
    day = date(2026, 12, 9)
    assert not bot.in_fomc_break(day, 264)
    assert bot.in_fomc_break(day, 265) and bot.in_fomc_break(day, 299)
    assert not bot.in_fomc_break(day, 300)
    assert not bot.in_fomc_break(date(2026, 12, 10), 280)


def test_lots_round_down_and_respect_the_limits():
    assert bot.lots_for(1.0, 10_000, 28_000, 0.01, 0.01, 1000) == 0.35
    assert bot.lots_for(0.0001, 10_000, 28_000, 0.01, 0.01, 1000) == 0.0
    assert bot.lots_for(1.0, 10_000, 1, 0.01, 0.01, 50) == 50


def test_stop_is_on_the_correct_side_and_respects_the_day_limit():
    long_stop = bot.stop_price(1, 100.0, 0.01, 10_000, 10_000, 1.0, 2)
    short_stop = bot.stop_price(-1, 100.0, 0.01, 10_000, 10_000, 1.0, 2)
    assert long_stop == 99.0 and short_stop == 101.0
    # Only $50 left before the day limit at 1,000 lots-worth of sensitivity
    assert bot.stop_price(1, 100.0, 0.5, 9750, 10_000, 1000.0, 2) == 99.95


def test_today_is_kept_only_for_the_bot():
    times = pd.date_range("2026-11-02 16:30", periods=60, freq="min")
    bars = pd.DataFrame({"time": times, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "tick_volume": 1.0,
                         "spread": 1.0})
    assert len(build_days(bars, "US100.cash").dates) == 0
    assert len(build_days(bars, "US100.cash", keep_today=True).dates) == 1


def test_dry_run_never_calls_order_send(monkeypatch):
    def forbidden(*_):
        raise AssertionError("order_send was called in a dry run")

    monkeypatch.setattr(bot.mt5, "order_send", forbidden)
    monkeypatch.setattr(bot.mt5, "symbol_info", lambda _: None)
    monkeypatch.setattr(bot, "load_state", lambda: bot.State())
    monkeypatch.setattr(bot, "journal", lambda *a, **k: None)
    assert bot.Bot(place_orders=False).send({"symbol": "US100.cash"}, "test")
