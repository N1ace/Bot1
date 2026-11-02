from collections import namedtuple
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import ftmo.bot as bot
from ftmo.backtest import build_days

real = bot.mt5
Reply = namedtuple("Reply", "retcode comment order deal volume price request")
LOGIN = 111
# Monday 10:00 New York, when US winter time applies. 15:00 UTC, 17:00 on the server
NOW = datetime(2026, 11, 2, 15, 0, 5, tzinfo=timezone.utc)
SERVER_EPOCH = datetime(2026, 11, 2, 17, 0, 0, tzinfo=timezone.utc).timestamp()


class FakeMT5:
    """The parts of MT5 the bot touches, with every order recorded."""

    def __getattr__(self, name):
        return getattr(real, name)

    def __init__(self):
        self.orders = []
        self.open_positions = []
        self.reply_code = real.TRADE_RETCODE_DONE
        self.login = LOGIN
        self.equity = 10_000.0
        self.tick_time = SERVER_EPOCH
        self.positions_fail = False
        self.connected = True

    def account_info(self):
        return SimpleNamespace(login=self.login, trade_mode=real.ACCOUNT_TRADE_MODE_DEMO, server="FTMO-Demo",
                               equity=self.equity, balance=10_000.0, currency="USD")

    def terminal_info(self):
        return SimpleNamespace(connected=self.connected, trade_allowed=True)

    def symbol_info(self, _):
        return SimpleNamespace(trade_tick_value=0.01, trade_tick_size=0.01, currency_profit="USD", point=0.01, digits=2,
                               volume_step=0.01, volume_min=0.01, volume_max=1000)

    def symbol_info_tick(self, _):
        return SimpleNamespace(time=int(self.tick_time), bid=28_000.0, ask=28_001.45)

    def positions_get(self, symbol=None):
        return None if self.positions_fail else tuple(self.open_positions)

    def order_send(self, request):
        self.orders.append(request)
        return Reply(self.reply_code, "ok", 1, 2, request["volume"], request["price"], request)

    def history_deals_get(self, *_):
        return (SimpleNamespace(type=real.DEAL_TYPE_BALANCE, time=1, profit=10_000.0, ticket=1,
                                _asdict=lambda: {"ticket": 1}),)

    def initialize(self):
        return True

    def last_error(self):
        return (1, "fake")


def position(direction=1, sl=27_900.0, ticket=7, opened=SERVER_EPOCH - 60):
    kind = real.POSITION_TYPE_BUY if direction > 0 else real.POSITION_TYPE_SELL
    return SimpleNamespace(type=kind, sl=sl, ticket=ticket, volume=0.3, magic=bot.MAGIC, time=int(opened))


@pytest.fixture
def fake(monkeypatch):
    mt5 = FakeMT5()
    monkeypatch.setattr(bot, "mt5", mt5)
    monkeypatch.setattr(bot, "utc_now", lambda: NOW)
    return mt5


def make_bot(tmp_path, place_orders=True):
    return bot.Bot(place_orders, LOGIN, tmp_path / "t")


def signal(monkeypatch, target, hard_stop=0.01):
    """Replace the market read with a chosen decision."""
    today = 0
    days = SimpleNamespace(dates=[1], close=pd.DataFrame([[28_000.0] * 390]).to_numpy())
    monkeypatch.setattr(bot, "volatility_spike", lambda *_: False)
    monkeypatch.setattr(bot.Bot, "read_days", lambda self, d, m: days)
    monkeypatch.setattr(bot.tactic, "levels", lambda d, s: SimpleNamespace(ready=[True], hard_stop=[hard_stop]))
    monkeypatch.setattr(bot.tactic, "decide", lambda *_: target)
    monkeypatch.setattr(bot.backtest, "position_sizes", lambda *_: [1.0])
    return today


def test_refuses_a_real_account(fake):
    account = fake.account_info()
    account.trade_mode = real.ACCOUNT_TRADE_MODE_REAL
    with pytest.raises(SystemExit):
        bot.check_account(account, False, None)


def test_refuses_a_server_not_named_demo(fake):
    account = fake.account_info()
    account.server = "FTMO-Server"
    with pytest.raises(SystemExit):
        bot.check_account(account, False, None)


def test_order_mode_must_name_the_exact_login(fake):
    bot.check_account(fake.account_info(), True, LOGIN)
    with pytest.raises(SystemExit):
        bot.check_account(fake.account_info(), True, 222)
    with pytest.raises(SystemExit):
        bot.check_account(fake.account_info(), True, None)


def test_refuses_a_symbol_that_breaks_the_sizing_assumption(fake):
    info = fake.symbol_info("x")
    info.trade_tick_value = 0.1
    with pytest.raises(SystemExit):
        bot.check_symbol(info, fake.account_info())
    with pytest.raises(SystemExit):
        bot.check_symbol(None, fake.account_info())


def test_state_from_another_account_is_refused(tmp_path):
    path = tmp_path / "s.json"
    bot.save_state(bot.State(login=1, start_balance=10_000), path)
    with pytest.raises(SystemExit):
        bot.load_state(path, 2)


def test_dry_and_demo_keep_separate_files():
    assert bot.file_stem(1, True) != bot.file_stem(1, False)
    assert "OneDrive" not in str(bot.BOT_DIR)


def test_clock_follows_new_york_across_the_us_clock_change():
    summer = datetime(2026, 10, 30, 13, 30, tzinfo=timezone.utc)
    winter = datetime(2026, 11, 2, 14, 30, tzinfo=timezone.utc)
    assert bot.new_york_clock(summer)[:2] == (date(2026, 10, 30), 0)
    assert bot.new_york_clock(winter)[:2] == (date(2026, 11, 2), 0)
    assert bot.new_york_clock(winter)[2] == datetime(2026, 11, 2, 16, 30)


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


def test_stop_uses_the_higher_of_the_two_limits():
    assert bot.equity_floor(9_301, 10_000) == 9_300
    assert bot.equity_floor(10_000, 10_000) == 9_700


def test_stop_is_on_the_correct_side_and_respects_the_floor():
    assert bot.stop_price(1, 100.0, 0.01, 10_000, 9_700, 1.0, 0.01) == 99.0
    assert bot.stop_price(-1, 100.0, 0.01, 10_000, 9_700, 1.0, 0.01) == 101.0
    assert bot.stop_price(1, 100.0, 0.5, 9_750, 9_700, 1000.0, 0.01) == 99.95


def test_negative_room_gives_a_stop_that_is_refused():
    stop = bot.stop_price(1, 28_001.45, 0.01, 9_600, 9_700, 0.3, 0.01)
    assert stop > 28_001.45
    assert not bot.stop_is_usable(1, stop, 28_000, 28_001.45)
    assert bot.stop_is_usable(1, 27_900, 28_000, 28_001.45)
    assert bot.stop_is_usable(-1, 28_100, 28_000, 28_001.45)
    assert not bot.stop_is_usable(-1, 27_900, 28_000, 28_001.45)


def test_today_is_kept_only_for_the_bot():
    times = pd.date_range("2026-11-02 16:30", periods=60, freq="min")
    bars = pd.DataFrame({"time": times, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "tick_volume": 1.0,
                         "spread": 1.0})
    assert len(build_days(bars, "US100.cash").dates) == 0
    assert len(build_days(bars, "US100.cash", keep_today=True).dates) == 1


def test_dry_run_never_calls_order_send(fake, monkeypatch, tmp_path):
    signal(monkeypatch, 1)
    fake.order_send = lambda _: pytest.fail("order_send was called in a dry run")
    make_bot(tmp_path, place_orders=False).step()


def test_a_real_order_is_sent_with_a_stop_and_journaled(fake, monkeypatch, tmp_path):
    signal(monkeypatch, 1)
    b = make_bot(tmp_path)
    b.step()
    assert len(fake.orders) == 1
    assert 0 < fake.orders[0]["sl"] < 28_000
    text = b.journal_path.read_text(encoding="utf-8")
    assert "order_intent" in text and "order_reply" in text and "step_error" not in text


def test_an_entry_is_sent_once_per_check(fake, monkeypatch, tmp_path):
    signal(monkeypatch, 1)
    b = make_bot(tmp_path)
    b.step()
    b.step()
    assert len(fake.orders) == 1


def test_failed_position_read_is_not_treated_as_flat(fake, monkeypatch, tmp_path):
    signal(monkeypatch, 1)
    fake.positions_fail = True
    make_bot(tmp_path).step()
    assert fake.orders == []


def test_reversal_closes_first_and_opens_only_after_a_clean_read(fake, monkeypatch, tmp_path):
    signal(monkeypatch, -1)
    fake.open_positions = [position(1)]
    b = make_bot(tmp_path)
    b.step()
    assert len(fake.orders) == 1 and fake.orders[0]["position"] == 7
    fake.open_positions = []
    b.step()
    assert len(fake.orders) == 2 and fake.orders[1]["type"] == real.ORDER_TYPE_SELL and "position" not in fake.orders[1]


def test_stale_price_opens_nothing(fake, monkeypatch, tmp_path):
    signal(monkeypatch, 1)
    fake.tick_time = SERVER_EPOCH - 600
    make_bot(tmp_path).step()
    assert fake.orders == []


def test_wrong_server_clock_opens_nothing(fake, monkeypatch, tmp_path):
    signal(monkeypatch, 1)
    fake.tick_time = SERVER_EPOCH + 3600
    make_bot(tmp_path).step()
    assert fake.orders == []


def test_position_left_from_an_earlier_day_is_closed_and_nothing_opens(fake, monkeypatch, tmp_path):
    signal(monkeypatch, 1)
    fake.open_positions = [position(1, opened=SERVER_EPOCH - 86_400)]
    make_bot(tmp_path).step()
    assert len(fake.orders) == 1 and fake.orders[0]["position"] == 7


def test_position_without_a_stop_is_closed(fake, monkeypatch, tmp_path):
    signal(monkeypatch, 1)
    fake.open_positions = [position(1, sl=0)]
    make_bot(tmp_path).step()
    assert len(fake.orders) == 1 and fake.orders[0]["position"] == 7


def test_two_positions_are_both_closed(fake, monkeypatch, tmp_path):
    signal(monkeypatch, 1)
    fake.open_positions = [position(1, ticket=7), position(1, ticket=8)]
    make_bot(tmp_path).step()
    assert {o["position"] for o in fake.orders} == {7, 8}


def test_failing_closes_back_off_and_dont_use_the_request_budget(fake, monkeypatch, tmp_path):
    signal(monkeypatch, 1)
    fake.open_positions = [position(1, sl=0)]
    fake.reply_code = 10006
    b = make_bot(tmp_path)
    for _ in range(5):
        b.step()
    assert len(fake.orders) == 1


def test_entries_stop_before_closes_at_the_request_limit(fake, monkeypatch, tmp_path):
    signal(monkeypatch, 1)
    b = make_bot(tmp_path)
    b.state.requests, b.state.requests_day, b.state.day = bot.ENTRY_REQUEST_LIMIT, "2026-11-02", "2026-11-02"
    b.state.day_start, b.state.start_balance = 10_000, 10_000
    b.step()
    assert fake.orders == []
    fake.open_positions = [position(1, sl=0)]
    b.step()
    assert len(fake.orders) == 1


def test_day_limit_closes_and_blocks_entries(fake, monkeypatch, tmp_path):
    signal(monkeypatch, 1)
    b = make_bot(tmp_path)
    b.step()
    fake.orders.clear()
    fake.equity = 9_690
    fake.open_positions = [position(1)]
    b.step()
    assert b.state.halted_day == "2026-11-02"
    assert len(fake.orders) == 1 and fake.orders[0]["position"] == 7


def test_total_limit_uses_the_first_deposit(fake, monkeypatch, tmp_path):
    signal(monkeypatch, 0)
    fake.equity = 9_299
    b = make_bot(tmp_path)
    b.step()
    assert b.state.start_balance == 10_000 and b.state.halted_for_good


def test_no_entry_on_a_short_market_day(fake, monkeypatch, tmp_path):
    signal(monkeypatch, 1)
    monkeypatch.setattr(bot, "NO_TRADE_DAYS", {date(2026, 11, 2)})
    make_bot(tmp_path).step()
    assert fake.orders == []


def test_account_change_during_a_run_stops_the_bot(fake, monkeypatch, tmp_path):
    signal(monkeypatch, 1)
    b = make_bot(tmp_path)
    fake.login = 222
    with pytest.raises(SystemExit):
        b.step()
    assert fake.orders == []


def test_a_bad_poll_does_not_end_the_bot(fake, monkeypatch, tmp_path):
    b = make_bot(tmp_path)
    monkeypatch.setattr(bot.Bot, "step", lambda self: 1 / 0)
    b.run(once=True)
    assert "step_error" in b.journal_path.read_text(encoding="utf-8")


def test_lost_connection_sends_nothing(fake, monkeypatch, tmp_path):
    signal(monkeypatch, 1)
    fake.connected = False
    make_bot(tmp_path).step()
    assert fake.orders == []


def test_close_out_time_closes_a_position(fake, monkeypatch, tmp_path):
    signal(monkeypatch, 1)
    late = NOW + timedelta(hours=5, minutes=59)  # 15:59 New York
    monkeypatch.setattr(bot, "utc_now", lambda: late)
    fake.tick_time = SERVER_EPOCH + (5 * 3600 + 59 * 60)
    fake.open_positions = [position(1)]
    make_bot(tmp_path).step()
    assert len(fake.orders) == 1 and fake.orders[0]["position"] == 7


def test_failed_tactic_exit_keeps_trying_after_the_check_minute(fake, monkeypatch, tmp_path):
    signal(monkeypatch, 0)
    fake.open_positions = [position(1)]
    fake.reply_code = 10006
    b = make_bot(tmp_path)
    b.step()
    assert b.pending_exit and len(fake.orders) == 1
    later = NOW + timedelta(minutes=5)
    monkeypatch.setattr(bot, "utc_now", lambda: later)
    fake.tick_time = SERVER_EPOCH + 300
    fake.reply_code = real.TRADE_RETCODE_DONE
    b.next_close_at = 0.0
    b.step()
    assert len(fake.orders) == 2
    fake.open_positions = []
    b.step()
    assert not b.pending_exit


def test_stale_price_still_tries_to_close_during_trading_hours(fake, monkeypatch, tmp_path):
    signal(monkeypatch, 1)
    fake.tick_time = SERVER_EPOCH - 600
    fake.open_positions = [position(1, sl=0, opened=SERVER_EPOCH - 700)]
    make_bot(tmp_path).step()
    assert len(fake.orders) == 1


def test_broken_history_still_closes_at_the_close_out(fake, monkeypatch, tmp_path):
    late = NOW + timedelta(hours=5, minutes=59)
    monkeypatch.setattr(bot, "utc_now", lambda: late)
    fake.tick_time = SERVER_EPOCH + (5 * 3600 + 59 * 60)
    fake.open_positions = [position(1)]
    fake.history_deals_get = lambda *_: None
    make_bot(tmp_path).step()
    assert len(fake.orders) == 1


def test_lost_connection_during_send_is_a_failed_send_not_an_exit(fake, tmp_path):
    b = make_bot(tmp_path)
    fake.account_info = lambda: None
    assert b.send({"symbol": "US100.cash"}, "test", bot.REQUEST_LIMIT) is False


def test_symbol_hours():
    assert bot.symbol_is_trading(datetime(2026, 11, 2, 12, 0))
    assert not bot.symbol_is_trading(datetime(2026, 11, 2, 23, 55))
    assert not bot.symbol_is_trading(datetime(2026, 11, 7, 12, 0))


def test_wide_spread_skips_the_entry(fake, monkeypatch, tmp_path):
    signal(monkeypatch, 1)
    monkeypatch.setattr(bot, "volatility_spike", lambda *_: False)
    fake.symbol_info_tick = lambda _: SimpleNamespace(time=int(SERVER_EPOCH), bid=28_000.0, ask=28_010.0)
    make_bot(tmp_path).step()
    assert fake.orders == []


def test_wide_spread_never_blocks_a_close(fake, monkeypatch, tmp_path):
    signal(monkeypatch, 1)
    fake.symbol_info_tick = lambda _: SimpleNamespace(time=int(SERVER_EPOCH), bid=28_000.0, ask=28_010.0)
    fake.open_positions = [position(1, sl=0)]
    make_bot(tmp_path).step()
    assert len(fake.orders) == 1


def test_volatility_spike_needs_an_unusual_range():
    shape = (30, 390)
    high, low = np.full(shape, 101.0), np.full(shape, 100.0)
    days = SimpleNamespace(high=high, low=low)
    assert not bot.volatility_spike(days, 29, 100)
    high[29, 97] = 110.0
    assert bot.volatility_spike(days, 29, 100)
    high[29, 97] = 101.0
    high[29, 100] = 110.0  # the bar still forming is ignored
    assert not bot.volatility_spike(days, 29, 100)


def test_volatility_spike_blocks_the_entry_and_uses_up_the_check(fake, monkeypatch, tmp_path):
    signal(monkeypatch, 1)
    monkeypatch.setattr(bot, "volatility_spike", lambda *_: True)
    b = make_bot(tmp_path)
    b.step()
    b.step()
    assert fake.orders == [] and b.state.last_check == "2026-11-02 30"


def test_volatility_spike_never_blocks_a_reversal_close(fake, monkeypatch, tmp_path):
    signal(monkeypatch, -1)
    monkeypatch.setattr(bot, "volatility_spike", lambda *_: True)
    fake.open_positions = [position(1)]
    b = make_bot(tmp_path)
    b.step()
    assert len(fake.orders) == 1 and fake.orders[0]["position"] == 7
    fake.open_positions = []
    b.step()
    assert len(fake.orders) == 1 and not b.pending_exit


def test_wide_spread_never_blocks_a_tactic_exit(fake, monkeypatch, tmp_path):
    signal(monkeypatch, 0)
    fake.symbol_info_tick = lambda _: SimpleNamespace(time=int(SERVER_EPOCH), bid=28_000.0, ask=28_010.0)
    fake.open_positions = [position(1)]
    make_bot(tmp_path).step()
    assert len(fake.orders) == 1


def test_journal_failure_does_not_stop_a_close(fake, monkeypatch, tmp_path):
    signal(monkeypatch, 1)
    fake.open_positions = [position(1, sl=0)]
    b = make_bot(tmp_path)
    b.journal_path = tmp_path / "missing" / "journal.jsonl"
    b.step()
    assert len(fake.orders) == 1


def test_dry_run_with_a_position_sends_nothing_and_sets_no_exit(fake, monkeypatch, tmp_path):
    signal(monkeypatch, 0)
    fake.order_send = lambda _: pytest.fail("order_send was called in a dry run")
    fake.open_positions = [position(1)]
    b = make_bot(tmp_path, place_orders=False)
    b.step()
    assert not b.pending_exit


def test_unknown_limits_block_entries(fake, monkeypatch, tmp_path):
    signal(monkeypatch, 1)
    fake.history_deals_get = lambda *_: None
    make_bot(tmp_path).step()
    assert fake.orders == []
