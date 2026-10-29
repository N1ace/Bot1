"""Daily Donchian ensemble (strategy-risk-spec.md section 2), as a pure function.

For each lookback L in {10, 20, 30, 60, 90, 150, 250} a "sub-signal" runs:
  - enter LONG when today's close > the highest close of the previous L days
  - (optional) enter SHORT when today's close < the lowest close of the previous L days
  - exit at a trailing stop = middle of the previous L days' range
    ((highest + lowest) / 2), which only moves in the trade's favour

The ensemble signal for a day is the average of the 7 sub-signals:
  1.0 = all 7 long, 3/7 = three of them long, 0 = flat
  (shorts count -short_weight each, so -0.5 = all 7 short at half weight).

The signal only uses closes up to and including that day (rolling windows are
shifted by one day), so it can be computed once for a whole history and then
read day by day without look-ahead. tests/test_strategy_risk.py checks this.

This function decides DIRECTION and STRENGTH only. How many coins to hold is
the risk module's job (risk/portfolio.py).
"""

from __future__ import annotations

import math

import pandas as pd

DEFAULT_LOOKBACKS = (10, 20, 30, 60, 90, 150, 250)


def sub_signal(close: pd.Series, lookback: int, allow_shorts: bool = False) -> pd.Series:
    """+1 / 0 / -1 position of one Donchian sub-signal for each day."""
    upper = close.shift(1).rolling(lookback).max()   # highest close of the PREVIOUS L days
    lower = close.shift(1).rolling(lookback).min()
    mid = (upper + lower) / 2
    out, pos, stop = [], 0, math.nan
    for c, up, lo, m in zip(close.to_numpy(), upper.to_numpy(), lower.to_numpy(), mid.to_numpy()):
        if math.isnan(up):
            out.append(0)
            continue
        if pos == 1:
            stop = max(stop, m)                       # trailing stop only moves up
            if c < stop:
                pos = 0
        elif pos == -1:
            stop = min(stop, m)                       # only moves down for a short
            if c > stop:
                pos = 0
        if pos == 0:                                  # (re-)entry check, also right after an exit
            if c > up:
                pos, stop = 1, m
            elif allow_shorts and c < lo:
                pos, stop = -1, m
        out.append(pos)
    return pd.Series(out, index=close.index, dtype=float)


def ensemble_signal(close: pd.Series, lookbacks=DEFAULT_LOOKBACKS, allow_shorts: bool = False,
                    short_weight: float = 0.5) -> pd.Series:
    """Average of the sub-signals; shorts weighted by short_weight. Range [-short_weight, 1]."""
    subs = pd.concat([sub_signal(close, L, allow_shorts) for L in lookbacks], axis=1)
    longs = subs.clip(lower=0).mean(axis=1)
    shorts = subs.clip(upper=0).mean(axis=1) * short_weight
    return (longs + shorts).rename("signal")
