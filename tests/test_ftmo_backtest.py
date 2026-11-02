from types import SimpleNamespace

import numpy as np
import pandas as pd

from ftmo.backtest import Days, challenge_once, position_sizes, run, sharpe
from ftmo.tactics import dax_overnight_range as dax


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


def test_layers_share_the_position_equally():
    days = with_trend(flat_days())
    single = fake_tactic(1)
    layered = fake_tactic(1)
    layered.LAYERS = (1, 2)
    layered.layer_settings = lambda settings, layer: settings
    assert np.allclose(run(days, layered, {}, 0.005).returns, run(days, single, {}, 0.005).returns)


def test_commission_is_charged_on_both_sides():
    days = with_trend(flat_days())
    free = run(days, fake_tactic(1), {}, 0.005).returns
    days.commission = 0.001
    assert (run(days, fake_tactic(1), {}, 0.005).returns[free != 0] < free[free != 0]).all()


def range_days(path):
    """One flat market around 100 with a 99-101 overnight range, then the chosen path after the open."""
    n, minutes, opened = 30, 720, 480
    prices = np.full((n, minutes), 100.0)
    highs, lows = prices.copy(), prices.copy()
    highs[:, 120:opened], lows[:, 120:opened] = 101.0, 99.0
    days = Days(pd.date_range("2024-01-01", periods=n), prices.copy(), highs, lows, prices.copy(), np.ones((n, minutes)),
                np.full((n, minutes), 0.01))
    days.close[:, -1] += np.resize([1.0, -1.0], n)
    for minute, (o, h, low, c) in path.items():
        days.open[20, minute], days.high[20, minute], days.low[20, minute], days.close[20, minute] = o, h, low, c
    return days


def test_breakout_hits_its_target_for_about_twice_the_risk():
    days = range_days({490: (101, 101, 101, 101), 500: (105, 105, 105, 105)})
    r = dax.simulate(days, dax.SETTINGS, 0.02)
    risk_share = 2 / 101 * position_sizes(days, 0.02, 4.0)[20]
    assert np.isclose(r.returns[20], 2 * risk_share, rtol=0.05)


def test_breakout_stop_wins_when_stop_and_target_share_a_bar():
    days = range_days({490: (101, 101, 101, 101), 500: (100, 105, 98, 100)})
    assert dax.simulate(days, dax.SETTINGS, 0.02).returns[20] < 0


def test_a_bar_touching_both_sides_skips_the_day():
    days = range_days({490: (100, 102, 98, 100)})
    assert dax.simulate(days, dax.SETTINGS, 0.02).returns[20] == 0


def test_range_uses_only_bars_before_the_open():
    days = range_days({490: (101, 300, 101, 101)})
    assert dax.levels(days, dax.SETTINGS).high[20] == 101


def trailing_tactic(level):
    tactic = fake_tactic(1, stop=0.5)
    tactic.CHECK_MINUTES = (30, 60)
    tactic.trail = lambda day, minute, position, lv: level
    return tactic


def test_tightened_stop_exits_between_checks():
    days = with_trend(flat_days())
    d = 20
    days.low[d, 100] = 98.0
    held = run(days, fake_tactic(1, stop=0.5), {}, 0.005).returns[d]
    trailed = run(days, trailing_tactic(99.0), {}, 0.005).returns[d]
    assert trailed < held


def test_a_stop_past_the_next_open_is_ignored():
    days = with_trend(flat_days())
    d = 20
    days.low[d, 100] = 98.0
    held = run(days, fake_tactic(1, stop=0.5), {}, 0.005).returns[d]
    assert run(days, trailing_tactic(150.0), {}, 0.005).returns[d] == held


def test_manage_can_leave_the_stop_alone():
    days = with_trend(flat_days())
    d = 20
    days.low[d, 100] = 98.0
    plain = run(days, fake_tactic(1, stop=0.5), {}, 0.005).returns[d]
    idle = fake_tactic(1, stop=0.5)
    idle.manage = lambda *_: None
    active = fake_tactic(1, stop=0.5)
    active.manage = lambda *_: 99.0
    assert run(days, idle, {}, 0.005).returns[d] == plain
    assert run(days, active, {}, 0.005).returns[d] < plain
