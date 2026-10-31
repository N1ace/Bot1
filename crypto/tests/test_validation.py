"""Walk-forward and Deflated Sharpe maths."""

import math

import numpy as np
import pandas as pd
import pytest

from cryptobot.backtest.validation import (annual_sharpe, deflated_sharpe_probability, expected_max_sharpe,
                                           max_drawdown, probabilistic_sharpe, walk_forward)


def returns(mean, std=0.01, n=2000, seed=0):
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(mean, std, n), index=pd.date_range("2018-01-01", periods=n, tz="UTC"))


def test_sharpe_and_drawdown():
    r = pd.Series([0.1, -0.5, 0.2])
    assert max_drawdown(r) == pytest.approx(-0.5)
    r = returns(0.001)
    assert annual_sharpe(r) == pytest.approx(r.mean() / r.std() * math.sqrt(365))


def test_expected_max_sharpe_grows_with_more_trials():
    few = expected_max_sharpe([0.0, 0.02, -0.02])
    many = expected_max_sharpe(list(np.random.default_rng(1).normal(0, 0.02, 100)))
    assert 0 < few < many
    assert expected_max_sharpe([0.05]) == 0.0           # one trial: no selection bias


def test_psr_high_for_real_edge_low_for_noise():
    assert probabilistic_sharpe(returns(0.002), 0.0) > 0.99
    assert probabilistic_sharpe(returns(0.0, seed=5), 0.0) < 0.9


def test_dsr_penalises_many_trials():
    edge = returns(0.0007, seed=2)                       # modest edge
    one = deflated_sharpe_probability(edge, [0.07])
    many = deflated_sharpe_probability(edge, list(np.random.default_rng(3).normal(0, 0.05, 200)))
    assert one > many


def test_walk_forward_chooses_on_train_and_scores_on_unseen_test():
    idx = pd.date_range("2018-01-01", periods=5 * 365, tz="UTC")
    good_then_bad = pd.Series(0.002, index=idx)
    good_then_bad[idx >= pd.Timestamp("2021-01-01", tz="UTC")] = -0.002
    steady = pd.Series(0.0005, index=idx) + returns(0, 0.0001, len(idx)).values
    good_then_bad = good_then_bad + returns(0, 0.0001, len(idx), seed=9).values
    wf = walk_forward({"A": good_then_bad, "B": steady}, train_days=3 * 365, test_days=182)
    first = wf.windows[0]
    assert first["chosen"] == "A"                         # best in the first 3 years...
    assert first["test_return"] < 0                       # ...and it fails on unseen data: that's the point
    assert wf.oos_returns.index.min() >= idx[0] + pd.Timedelta(days=3 * 365)
