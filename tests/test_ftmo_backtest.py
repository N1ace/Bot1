from types import SimpleNamespace

import numpy as np
import pandas as pd

from ftmo.backtest import Days, challenge_once, run, sharpe


def flat_days(n=30, price=100.0):
    shape = (n, 390)
    prices = np.full(shape, price)
    return Days(pd.date_range("2024-01-01", periods=n), prices.copy(), prices.copy(), prices.copy(), prices.copy(),
                np.ones(shape), np.full(shape, 0.1))


def fake_tactic(direction, stop=0.01):
    def levels(days, settings):
        return SimpleNamespace(ready=np.ones(len(days.dates), bool), hard_stop=np.full(len(days.dates), stop))

    def decide(day, minute, price, position, lv):
        return direction

    return SimpleNamespace(levels=levels, decide=decide, CHECK_MINUTES=(30, 60), PAPER_VOL_TARGET=0.02,
                           PAPER_MAX_EXPOSURE=4.0)


def with_trend(days):
    # Enough day-to-day movement for sizing to start
    days.close[:, -1] += np.resize([1.0, -1.0], len(days.dates))
    return days


def test_flat_market_loses_exactly_the_costs():
    days = with_trend(flat_days())
    result = run(days, fake_tactic(1), {}, 0.005)
    active = result.returns[result.returns != 0]
    assert len(active) > 0 and (active < 0).all()


def test_double_costs_cost_double():
    days = with_trend(flat_days())
    once = run(days, fake_tactic(1), {}, 0.005).returns.sum()
    twice = run(days, fake_tactic(1), {}, 0.005, cost_mult=2).returns.sum()
    assert np.isclose(twice, 2 * once)


def test_gap_through_stop_fills_at_the_worse_price():
    days = with_trend(flat_days())
    d = 20
    days.open[d, 100:] = 90.0
    days.low[d, 100:] = 90.0
    result = run(days, fake_tactic(1, stop=0.01), {}, 0.005)
    assert result.worst[d] < 0
    assert result.returns[d] < -0.0005


def test_day_stop_caps_a_crash_unless_it_gaps():
    days = with_trend(flat_days())
    d = 20
    days.low[d, 100:] = 50.0
    days.close[d, 100:] = 50.0
    days.open[d, 101:] = 50.0
    result = run(days, fake_tactic(1, stop=0.9), {}, 0.02)
    assert -0.036 <= result.returns[d] < -0.029


def test_challenge_needs_both_steps_and_four_days():
    returns = np.array([0.03] * 4 + [0.02] * 4)
    assert challenge_once(returns, np.zeros(8))[0] == "passed"
    assert challenge_once(np.array([0.11, 0.0, 0.0]), np.zeros(3))[0] == "unfinished"


def test_challenge_fails_at_the_bot_limits():
    assert challenge_once(np.array([-0.08]), np.array([-0.08]))[0] == "failed"
    assert challenge_once(np.array([0.05, -0.051]), np.array([0.0, -0.051]))[0] == "failed"


def test_sharpe_of_flat_is_zero():
    assert sharpe(np.zeros(10)) == 0.0
