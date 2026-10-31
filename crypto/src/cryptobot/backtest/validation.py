"""Honest-testing tools (strategy-risk-spec.md section 7, build step 6).

walk_forward   Repeatedly: choose the variant with the best Sharpe on the previous
               3 years ("train"), then record how that choice did on the NEXT 6
               months ("test"), which it never saw. Joining all test periods gives an
               out-of-sample result - the closest a backtest gets to "live".
deflated_sharpe_probability
               Bailey & Lopez de Prado (2014). If you try N variants and keep the best,
               the best one looks good partly by luck. The DSR asks: how likely is it
               that the true Sharpe is above what the best of N lucky tries would show?
               > 0.95 = probably a real edge.
All Sharpe ratios inside the DSR maths are per-day (not annualised).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

import pandas as pd

EULER_GAMMA = 0.5772156649
N01 = NormalDist()


def daily_sharpe(returns: pd.Series) -> float:
    r = returns.dropna()
    return float(r.mean() / r.std()) if len(r) > 1 and r.std() > 0 else 0.0


def annual_sharpe(returns: pd.Series) -> float:
    return daily_sharpe(returns) * math.sqrt(365)


def max_drawdown(returns: pd.Series) -> float:
    equity = (1 + returns.fillna(0)).cumprod()
    return float((equity / equity.cummax() - 1).min())


def expected_max_sharpe(trial_sharpes: list[float]) -> float:
    """Sharpe the best of N trials would show by luck alone (per-day units)."""
    n = len(trial_sharpes)
    if n < 2:
        return 0.0
    var = pd.Series(trial_sharpes).var()
    return math.sqrt(var) * ((1 - EULER_GAMMA) * N01.inv_cdf(1 - 1 / n) +
                             EULER_GAMMA * N01.inv_cdf(1 - 1 / (n * math.e)))


def probabilistic_sharpe(returns: pd.Series, benchmark_sr: float) -> float:
    """Probability that the true per-day Sharpe exceeds benchmark_sr, allowing for
    skew and fat tails of the returns."""
    r = returns.dropna()
    t = len(r)
    if t < 30 or r.std() == 0:
        return 0.0
    sr = daily_sharpe(r)
    skew = float(r.skew())
    kurt = float(r.kurt()) + 3            # pandas gives excess kurtosis
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if denom <= 0:
        return 0.0
    return N01.cdf((sr - benchmark_sr) * math.sqrt(t - 1) / math.sqrt(denom))


def deflated_sharpe_probability(returns: pd.Series, trial_sharpes: list[float]) -> float:
    return probabilistic_sharpe(returns, expected_max_sharpe(trial_sharpes))


@dataclass
class WalkForwardResult:
    oos_returns: pd.Series                          # stitched test-period returns
    windows: list[dict]                             # per window: dates, chosen variant, scores


def walk_forward(variant_returns: dict[str, pd.Series], train_days: int = 3 * 365, test_days: int = 182,
                 first_day: pd.Timestamp | None = None) -> WalkForwardResult:
    """Choose by train Sharpe, evaluate on the following test window, roll forward by test_days."""
    frame = pd.DataFrame(variant_returns).sort_index()
    start = first_day or frame.dropna(how="all").index[0]
    windows, pieces = [], []
    train_start = start
    while True:
        train_end = train_start + pd.Timedelta(days=train_days)
        test_end = train_end + pd.Timedelta(days=test_days)
        if train_end >= frame.index[-1]:
            break
        train = frame[(frame.index >= train_start) & (frame.index < train_end)]
        test = frame[(frame.index >= train_end) & (frame.index < test_end)]
        if test.empty:
            break
        scores = {name: annual_sharpe(train[name]) for name in frame}
        chosen = max(scores, key=scores.get)
        pieces.append(test[chosen])
        windows.append({"train": f"{train.index[0]:%Y-%m-%d}..{train.index[-1]:%Y-%m-%d}",
                        "test": f"{test.index[0]:%Y-%m-%d}..{test.index[-1]:%Y-%m-%d}",
                        "chosen": chosen, "train_sharpe": scores[chosen],
                        "test_sharpe": annual_sharpe(test[chosen]), "test_return": float((1 + test[chosen]).prod() - 1)})
        train_start = train_start + pd.Timedelta(days=test_days)
    return WalkForwardResult(pd.concat(pieces) if pieces else pd.Series(dtype=float), windows)
