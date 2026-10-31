"""Trade history and statistics (reporting/trades.py) + Telegram commands for them."""

import math
from datetime import date, datetime, timezone

import pytest

from cryptobot.advisor.store import AdvisorStore
from cryptobot.models import Fill, Side
from cryptobot.observer.daily import handle_commands
from cryptobot.reporting import trades as tr
from cryptobot.storage import TradeStore


def fill(env_store, side, qty, price, fee, pnl, when, symbol="BTCUSDT"):
    env_store.record_fill(Fill(order_id="1", symbol=symbol, side=Side(side), quantity=qty, price=price, fee=fee,
                               fee_asset="USDT", timestamp=datetime.fromisoformat(when).replace(tzinfo=timezone.utc),
                               realized_pnl=pnl))


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "trades.sqlite"
    demo = TradeStore(path, "demo", "futures", "FUTURES")
    fill(demo, "buy", 0.001, 80_000, 0.04, 0, "2026-10-20T10:00:00")      # winning long
    fill(demo, "sell", 0.001, 82_000, 0.04, 2.0, "2026-10-21T10:00:00")
    fill(demo, "sell", 0.002, 81_000, 0.08, 0, "2026-10-28T09:00:00")     # losing short
    fill(demo, "buy", 0.002, 81_500, 0.08, -1.0, "2026-10-28T15:00:00")
    fill(demo, "buy", 0.01, 2_000, 0.01, 0, "2026-10-29T12:00:00", "ETHUSDT")   # still open
    demo.record_funding(int(datetime(2026, 10, 29, 16, tzinfo=timezone.utc).timestamp() * 1000), "ETHUSDT",
                        "USDT", -0.02)
    live = TradeStore(path, "live", "futures", "FUTURES")
    fill(live, "buy", 0.001, 80_000, 0.04, 0, "2026-10-28T11:00:00")
    demo.close()
    live.close()
    return path


def test_period_ranges():
    start, end, _ = tr.period_range("yesterday", date(2026, 10, 30))
    assert start.date() == date(2026, 10, 29) and end.date() == date(2026, 10, 30)
    start, end, _ = tr.period_range("week", date(2026, 10, 30))
    assert (end - start).days == 7


def test_round_trips_and_open_positions(db):
    fills = tr.load_fills(db, *tr.custom_range(date(2026, 10, 1), date(2026, 10, 31))[:2])
    trips = tr.round_trips(fills[fills["environment"] == "demo"])
    assert [(t.symbol, t.side, t.closed is not None) for t in trips] == [
        ("BTCUSDT", "long", True), ("BTCUSDT", "short", True), ("ETHUSDT", "long", False)]
    assert trips[0].net == pytest.approx(2.0 - 0.08)
    assert trips[1].entry_price == 81_000 and trips[1].exit_price == 81_500


def test_statistics_split_by_environment(db):
    start, end, _ = tr.custom_range(date(2026, 10, 1), date(2026, 10, 31))
    stats = tr.statistics(tr.load_fills(db, start, end), tr.load_funding(db, start, end))
    demo, live = stats["demo"], stats["live"]
    assert demo["trades_closed"] == 2 and demo["trades_open"] == 1
    assert demo["wins"] == 1 and demo["losses"] == 1 and demo["win_rate"] == 0.5
    assert demo["funding"] == pytest.approx(-0.02)
    assert demo["net"] == pytest.approx(2.0 - 1.0 - (0.04 + 0.04 + 0.08 + 0.08 + 0.01) - 0.02)
    assert live["trades_closed"] == 0 and live["trades_open"] == 1      # never mixed with demo


def test_single_day(db):
    text = tr.report(db, *tr.day_range(date(2026, 10, 28)))
    assert "SHORT" in text and "[live]" in text and "2026-10-20" not in text


def test_empty_period_and_missing_db(tmp_path):
    assert "No trades" in tr.report(db := tmp_path / "none.sqlite", *tr.day_range(date(2026, 1, 1)))
    assert not db.exists()          # reading never creates a database


def test_trade_opened_before_period_is_not_misread(db):
    """Reviewer finding: /today must not show a fake SHORT for a long opened yesterday."""
    text = tr.report(db, *tr.day_range(date(2026, 10, 21)))       # only the closing SELL is on this day
    assert "LONG" in text and "SHORT" not in text
    stats = tr.statistics(tr.load_fills(db, *tr.day_range(date(2026, 10, 21))[:2]), tr.load_funding(
        db, *tr.day_range(date(2026, 10, 21))[:2]), tr.trips_for_period(
        tr.load_fills(db, tr.EPOCH, tr.day_range(date(2026, 10, 21))[1]), *tr.day_range(date(2026, 10, 21))[:2]),
        tr.day_range(date(2026, 10, 21))[0])
    assert stats["demo"]["trades_closed"] == 1 and stats["demo"]["wins"] == 1


def test_fees_in_other_assets_are_not_added_to_usdt(tmp_path):
    path = tmp_path / "bnb.sqlite"
    s = TradeStore(path, "demo", "futures", "FUTURES")
    s.record_fill(Fill(order_id="1", symbol="BTCUSDT", side=Side.BUY, quantity=0.001, price=80_000, fee=0.0001,
                       fee_asset="BNB", timestamp=datetime(2026, 10, 28, tzinfo=timezone.utc)))
    s.close()
    text = tr.report(path, *tr.day_range(date(2026, 10, 28)), what="stats")
    assert "fees -0.00 USDT" in text and "0.0001 BNB" in text


def test_telegram_history_commands(db):
    a = AdvisorStore(":memory:")
    replies = handle_commands(a, ["/day 2026-10-28", "/stats all", "/day 2026-13-01", "/hello"], str(db))
    assert "SHORT" in replies[0]
    assert "Statistics - all time" in replies[1] and "NET" in replies[1]
    assert "Not a valid date" in replies[2]
    assert "/today" in replies[3]           # help text


def test_a_missing_table_is_empty_but_a_real_read_error_is_raised(tmp_path):
    import sqlite3
    import pytest
    from cryptobot.reporting.trades import _read
    db = tmp_path / "x.sqlite"
    sqlite3.connect(db).close()
    assert _read(db, "SELECT * FROM fills", ()).empty
    with pytest.raises(Exception):
        _read(db, "SELECT nonsense FROM", ())
