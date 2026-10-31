"""Backtester math on small synthetic data (no downloads, no network)."""

import pandas as pd
import pytest

from cryptobot.backtest.data import (ChecksumError, months, parse_funding_csv, parse_kline_csv, to_ms,
                                     verify_checksum)
from cryptobot.backtest.engine import (Backtester, BacktestConfig, Target, buy_and_hold, performance_metrics,
                                       round_qty)

SYM = "BTCUSDT"


def bars(rows):
    """rows: list of (open, high, low, close) for consecutive days from 2024-01-01."""
    idx = pd.date_range("2024-01-01", periods=len(rows), freq="D", tz="UTC")
    return pd.DataFrame(rows, index=idx, columns=["open", "high", "low", "close"]).assign(volume=1.0)


def cfg(**kw):
    base = dict(initial_equity=10_000, taker_fee=0.0, slippage_bps={"default": 0},
                qty_step={}, min_notional={}, use_funding=True)
    return BacktestConfig(**(base | kw))


def script(plan):
    """Strategy that returns plan[day_index] (a Target or None)."""
    def strategy(symbol, history, state):
        return plan.get(len(history) - 1)
    return strategy


FLAT = [(100, 101, 99, 100)] * 5


# ---- fills, fees, slippage -------------------------------------------------------

def test_signal_fills_next_day_open_with_fee_and_slippage():
    data = bars([(100, 101, 99, 100), (110, 111, 109, 110), (110, 111, 109, 110)])
    bt = Backtester(cfg(taker_fee=0.0005, slippage_bps={"default": 10}), {SYM: data})
    res = bt.run(script({0: Target(qty=1)}))
    f = res.fills.iloc[0]
    assert f["date"] == data.index[1]                 # decided day 0, filled day 1
    assert f["price"] == pytest.approx(110 * 1.001)   # buy pays up the slippage
    assert f["fee"] == pytest.approx(110.11 * 0.0005)
    assert res.totals["slippage_cost"] == pytest.approx(0.11)


def test_sell_slippage_lowers_price_and_pnl_accounting():
    data = bars([(100, 100, 100, 100), (100, 100, 100, 100), (120, 120, 120, 120), (120, 120, 120, 120)])
    res = Backtester(cfg(), {SYM: data}).run(script({0: Target(qty=2), 2: Target(qty=0)}))
    assert res.totals["realized_pnl"] == pytest.approx(2 * (120 - 100))
    assert res.equity.iloc[-1] == pytest.approx(10_000 + 40)


def test_strategy_never_sees_the_future():
    data = bars([(100 + i, 101 + i, 99 + i, 100 + i) for i in range(6)])
    seen = []

    def spy(symbol, history, state):
        seen.append((history.index[-1], state.date))
        return None

    Backtester(cfg(), {SYM: data}).run(spy)
    assert all(last == today for last, today in seen)  # history ends at today's close


def test_changing_tomorrow_does_not_change_todays_decision():
    a = bars([(100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100)])
    b = a.copy()
    b.iloc[2] = [50, 51, 49, 50, 1.0]                      # wildly different future
    decisions = {}

    def record(name):
        def strategy(symbol, history, state):
            decisions.setdefault(name, []).append(history["close"].iloc[-1])
            return None
        return strategy

    Backtester(cfg(), {SYM: a}).run(record("a"))
    Backtester(cfg(), {SYM: b}).run(record("b"))
    assert decisions["a"][:2] == decisions["b"][:2]


# ---- exchange rules ---------------------------------------------------------------

def test_round_qty_toward_zero():
    assert round_qty(0.0059, 0.001) == pytest.approx(0.005)
    assert round_qty(-0.0059, 0.001) == pytest.approx(-0.005)
    assert round_qty(0.0009, 0.001) == 0.0


def test_orders_below_min_notional_are_skipped():
    res = Backtester(cfg(qty_step={SYM: 0.001}, min_notional={SYM: 50}), {SYM: bars(FLAT)}).run(
        script({0: Target(qty=0.4)}))          # 0.4 * 100 = 40 USDT < 50
    assert res.fills.empty


# ---- funding --------------------------------------------------------------------

def funding_df(times_rates):
    idx = pd.to_datetime([t for t, _ in times_rates], utc=True, format="ISO8601")
    return pd.DataFrame({"interval_hours": 8, "rate": [r for _, r in times_rates]}, index=idx)


def test_long_pays_positive_funding_short_receives():
    fund = funding_df([("2024-01-02 08:00", 0.0001)])
    long_res = Backtester(cfg(), {SYM: bars(FLAT)}, {SYM: fund}).run(script({0: Target(qty=1)}))
    short_res = Backtester(cfg(), {SYM: bars(FLAT)}, {SYM: fund}).run(script({0: Target(qty=-1)}))
    assert long_res.totals["funding"] == pytest.approx(-0.01)    # 1 x 100 x 0.0001
    assert short_res.totals["funding"] == pytest.approx(+0.01)


def test_funding_at_open_uses_overnight_position():
    """00:00 settlement happens before the open fill: a position opened at today's open doesn't pay it,
    and one closed at today's open still does."""
    # Days: 01-01 (decide buy), 01-02 (filled at open), 01-03 (decide close), 01-04 (closed at open)
    fund = funding_df([("2024-01-02 00:00:00.005", 0.001), ("2024-01-04 00:00", 0.001)])
    res = Backtester(cfg(), {SYM: bars(FLAT)}, {SYM: fund}).run(script({0: Target(qty=1), 2: Target(qty=0)}))
    # 01-02 00:00 settles just BEFORE the buy fills -> not charged.
    # 01-04 00:00 settles just BEFORE the close fills -> charged (held overnight).
    assert list(res.funding["time"]) == [pd.Timestamp("2024-01-04", tz="UTC")]
    assert res.totals["funding"] == pytest.approx(-0.1)          # 1 x 100 x 0.001

    # Closing one day earlier (filled at the 01-03 open) avoids the 01-04 settlement.
    res2 = Backtester(cfg(), {SYM: bars(FLAT)}, {SYM: fund}).run(script({0: Target(qty=1), 1: Target(qty=0)}))
    assert res2.totals["funding"] == 0


def test_funding_can_be_disabled():
    fund = funding_df([("2024-01-02 08:00", 0.01)])
    res = Backtester(cfg(use_funding=False), {SYM: bars(FLAT)}, {SYM: fund}).run(script({0: Target(qty=1)}))
    assert res.totals["funding"] == 0


# ---- stops ----------------------------------------------------------------------

def stop_data():
    # day0 decide; day1 fill at 100; day2 gaps down: open 90, low 85 (stop at 95)
    return bars([(100, 100, 100, 100), (100, 100, 100, 100), (90, 92, 85, 88), (88, 88, 88, 88)])


def test_gap_stop_spec_strict_fills_at_bar_extreme():
    res = Backtester(cfg(stop_fill="spec_strict"), {SYM: stop_data()}).run(script({0: Target(qty=1, stop=95)}))
    stop = res.fills[res.fills["reason"] == "stop_gap"].iloc[0]
    assert stop["price"] == 85


def test_gap_stop_open_if_gap_fills_at_open():
    res = Backtester(cfg(stop_fill="open_if_gap"), {SYM: stop_data()}).run(script({0: Target(qty=1, stop=95)}))
    assert res.fills[res.fills["reason"] == "stop_gap"].iloc[0]["price"] == 90


def test_normal_stop_fills_at_stop_minus_slippage():
    data = bars([(100, 100, 100, 100), (100, 100, 100, 100), (99, 99, 94, 96), (96, 96, 96, 96)])
    res = Backtester(cfg(slippage_bps={"default": 10}), {SYM: data}).run(script({0: Target(qty=1, stop=95)}))
    stop = res.fills[res.fills["reason"] == "stop"].iloc[0]
    assert stop["price"] == pytest.approx(95 * 0.999)


def test_stop_uses_mark_price_range():
    trade = bars([(100, 100, 100, 100), (100, 100, 100, 100), (99, 99, 90, 96), (96, 96, 96, 96)])
    mark = bars([(100, 100, 100, 100), (100, 100, 100, 100), (99, 99, 97, 96), (96, 96, 96, 96)])
    res = Backtester(cfg(), {SYM: trade}, mark_bars={SYM: mark}).run(script({0: Target(qty=1, stop=95)}))
    assert (res.fills["reason"] == "signal").all()        # trade price wicked to 90, mark only to 97


def test_short_stop_above():
    data = bars([(100, 100, 100, 100), (100, 100, 100, 100), (101, 106, 100, 104), (104, 104, 104, 104)])
    res = Backtester(cfg(), {SYM: data}).run(script({0: Target(qty=-1, stop=105)}))
    stop = res.fills[res.fills["reason"] == "stop"].iloc[0]
    assert stop["price"] == 105 and stop["qty"] == 1


# ---- liquidation ----------------------------------------------------------------

def test_liquidation_closes_everything_and_stops_trading():
    # 3x long (30,000 notional on 10,000 equity), price falls 40% intraday -> blown.
    data = bars([(100, 100, 100, 100), (100, 100, 100, 100), (95, 96, 60, 70), (70, 70, 70, 70),
                 (70, 70, 70, 70)])
    res = Backtester(cfg(), {SYM: data}).run(script({0: Target(qty=300), 3: Target(qty=300)}))
    liq = res.fills[res.fills["reason"] == "liquidation"]
    assert len(liq) == 1 and liq.iloc[0]["price"] == 60
    assert res.totals["liquidated"] is True
    assert len(res.fills) == 2                     # entry + liquidation, the day-3 order was never sent
    assert res.equity.iloc[-1] >= 0                # insurance fund: balance never below zero


def test_no_liquidation_with_moderate_loss():
    data = bars([(100, 100, 100, 100), (100, 100, 100, 100), (95, 96, 80, 85), (85, 85, 85, 85)])
    res = Backtester(cfg(), {SYM: data}).run(script({0: Target(qty=100)}))   # 1x, -20%
    assert res.totals["liquidated"] is False


# ---- buy & hold sanity, metrics -------------------------------------------------

def test_buy_and_hold_equals_price_change_minus_costs():
    data = bars([(100, 100, 100, 100)] * 2 + [(150, 150, 150, 150)] * 3)
    res = Backtester(cfg(taker_fee=0.001), {SYM: data}).run(buy_and_hold())
    qty = 10_000 / 100
    assert res.equity.iloc[-1] == pytest.approx(10_000 + qty * 50 - qty * 100 * 0.001)


def test_performance_metrics():
    eq = pd.Series([100, 110, 99, 120], index=pd.date_range("2024-01-01", periods=4, tz="UTC"))
    m = performance_metrics(eq)
    assert m["max_drawdown"] == pytest.approx(99 / 110 - 1)
    assert m["final_equity"] == 120


# ---- data parsing ---------------------------------------------------------------

def test_kline_csv_with_and_without_header():
    no_header = "1577836800000,7189.43,7260.43,7170.15,7197.57,56801.329,1577923199999,1,2,3,4,0\n"
    with_header = "open_time,open,high,low,close,volume,close_time,a,b,c,d,ignore\n" + no_header
    assert parse_kline_csv(no_header) == parse_kline_csv(with_header)
    assert parse_kline_csv(no_header)[0][:2] == (1577836800000, 7189.43)


def test_funding_csv():
    text = "calc_time,funding_interval_hours,last_funding_rate\n1788220800005,8,0.00008482\n"
    assert parse_funding_csv(text) == [(1788220800005, 8, 0.00008482)]


def test_microsecond_timestamps_converted():
    assert to_ms(1788220800000000) == 1788220800000
    assert to_ms(1788220800000) == 1788220800000


def test_checksum_verification():
    import hashlib
    content = b"zipbytes"
    good = hashlib.sha256(content).hexdigest() + "  file.zip"
    assert verify_checksum(content, good) == hashlib.sha256(content).hexdigest()
    with pytest.raises(ChecksumError):
        verify_checksum(b"tampered", good)


def test_months_range():
    assert months("2025-11", "2026-02") == ["2025-11", "2025-12", "2026-01", "2026-02"]
