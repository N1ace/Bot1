"""Satellite sleeve (4h): regime timing, tactics, no look-ahead, caps, kill switch."""

import dataclasses

import numpy as np
import pandas as pd

from cryptobot.backtest.engine import Backtester, BacktestConfig, PortfolioState, Position, performance_metrics
from cryptobot.strategies.satellite import (SatelliteConfig, SatellitePortfolio, load_satellite_config, precompute,
                                            to_daily)


def bars_4h(closes, start="2022-01-01"):
    idx = pd.date_range(start, periods=len(closes), freq="4h", tz="UTC")
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"open": c.shift(1).fillna(c.iloc[0]), "high": c * 1.004, "low": c * 0.996,
                         "close": c, "volume": 1.0})


def random_walk(n=6 * 400, seed=0, drift=0.0):
    rng = np.random.default_rng(seed)
    return 100 * np.exp(np.cumsum(rng.normal(drift, 0.01, n)))


def test_to_daily_keeps_only_complete_days():
    b = bars_4h(range(1, 6 * 3 + 3))                 # 3 full days + 2 bars of a 4th
    d = to_daily(b)
    assert len(d) == 3 and d["close"].iloc[0] == 6 and d["open"].iloc[1] == b["open"].iloc[6]


def test_regime_of_a_day_applies_only_from_the_next_day():
    cfg = SatelliteConfig()
    b = bars_4h(random_walk(seed=1))
    pre = precompute(b, None, cfg)
    # Change everything from day 200 on: regimes of bars on days <= 200 must not move,
    # because they only use days that finished before the bar.
    cut = pd.Timestamp("2022-01-01", tz="UTC") + pd.Timedelta(days=200)
    b2 = b.copy()
    b2.loc[b2.index >= cut, ["open", "high", "low", "close"]] *= 3
    pre2 = precompute(b2, None, cfg)
    upto = pre.index < cut + pd.Timedelta(days=1)
    assert (pre.loc[upto, "regime"] == pre2.loc[upto, "regime"]).all()


def test_decisions_never_depend_on_future_bars():
    sat, risk, _ = load_satellite_config()
    closes = random_walk(seed=3)
    b = {"BTCUSDT": bars_4h(closes)}
    cfg = BacktestConfig(bar=pd.Timedelta(hours=4))
    full = Backtester(cfg, b).run(SatellitePortfolio(b, sat, risk))
    cut = b["BTCUSDT"].index[1800]
    short = {"BTCUSDT": b["BTCUSDT"].loc[:cut]}
    part = Backtester(cfg, short).run(SatellitePortfolio(short, sat, risk))
    assert np.allclose(full.equity.loc[:cut].values, part.equity.values)


def test_mean_reversion_entry_and_time_exit():
    cfg = dataclasses.replace(SatelliteConfig(), tactics=("meanrev",))
    sat_risk = load_satellite_config()[1]
    b = bars_4h(random_walk(seed=4))
    p = SatellitePortfolio({"BTCUSDT": b}, cfg, sat_risk)
    d = p.data["BTCUSDT"]
    i = len(d) - 1
    d.iloc[i, d.columns.get_loc("regime")] = "chop"
    d.iloc[i, d.columns.get_loc("close")] = d["bb_low"].iloc[i] * 0.99
    d.iloc[i, d.columns.get_loc("rsi")] = 20
    tactic, side, dist = p._entry("BTCUSDT", i)
    assert tactic == "meanrev" and side == 1 and dist == cfg.mr_stop_atr * d["atr"].iloc[i]


def test_new_trades_shrink_to_fit_caps_without_touching_open_ones():
    _, risk, _ = load_satellite_config()
    p = SatellitePortfolio({"BTCUSDT": bars_4h(random_walk(n=600))}, SatelliteConfig(), risk)
    held = {"BTCUSDT": (10.0, 50.0, 1000.0)}             # risk 500 = 2.5% of 20k
    new = {"ETHUSDT": (20.0, 25.0, 500.0)}               # wants another 500
    scale = p._room_for_new(20_000, held, new, {("BTCUSDT", "ETHUSDT"): 0.9})
    assert 0 < scale < 1
    total_heat = (500 + 500 * scale) / 20_000
    assert total_heat <= risk.max_heat + 1e-6


def test_kill_switch_flattens_and_stays_flat():
    sat, risk, _ = load_satellite_config()
    b = {"BTCUSDT": bars_4h(random_walk(n=600))}
    p = SatellitePortfolio(b, sat, risk)
    date = b["BTCUSDT"].index[300]
    pos = {"BTCUSDT": Position(qty=1.0, entry_price=100, stop=90)}
    p.targets({"BTCUSDT": b["BTCUSDT"].loc[:date]}, PortfolioState(20_000, pos, date))
    out = p.targets({"BTCUSDT": b["BTCUSDT"].loc[:date]},
                    PortfolioState(20_000 * 0.64, pos, date + pd.Timedelta(hours=4)))
    assert out["BTCUSDT"].qty == 0 and p.breakers.stopped


def test_metrics_of_4h_curve_are_daily():
    idx = pd.date_range("2022-01-01", periods=6 * 10, freq="4h", tz="UTC")
    eq = pd.Series(np.linspace(100, 110, len(idx)), index=idx)
    assert performance_metrics(eq)["days"] == 10
