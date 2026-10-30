"""Strongest vs weakest coins: universe without look-ahead, picks, delisting, stops."""

import numpy as np
import pandas as pd
import pytest

from cryptobot.backtest.engine import Backtester, BacktestConfig, Position, PortfolioState, Target
from cryptobot.strategies.xs_momentum import XSConfig, XSMomentum, load_xs_config, panels, pick


def coin(drift, n=200, seed=0, volume=1e6, start="2022-01-01"):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="D", tz="UTC")
    c = pd.Series(100 * np.exp(np.cumsum(rng.normal(drift, 0.02, n))), index=idx)
    return pd.DataFrame({"open": c.shift(1).fillna(c.iloc[0]), "high": c * 1.01, "low": c * 0.99, "close": c,
                         "volume": 1.0, "quote_volume": volume})


def market(n_coins=20, n=200):
    return {f"C{i}USDT": coin(-0.01 + 0.001 * i, n=n, seed=i) for i in range(n_coins)}


def test_pick_longs_strongest_shorts_weakest_and_needs_enough_coins():
    cfg = XSConfig()
    bars = market()
    p = panels(bars, None, cfg)
    d = p["close"].index[150]
    longs, shorts = pick(p["score"].loc[d], p["liquidity"].loc[d], p["age"].loc[d], p["close"].loc[d], cfg)
    assert len(longs) == 4 and len(shorts) == 4 and not set(longs) & set(shorts)
    rank = p["score"].loc[d].sort_values()
    assert set(shorts) == set(rank.index[:4]) and set(longs) == set(rank.index[-4:])
    few = dict(list(bars.items())[:10])
    q = panels(few, None, cfg)
    assert pick(q["score"].loc[d], q["liquidity"].loc[d], q["age"].loc[d], q["close"].loc[d], cfg) == ([], [])


def test_universe_ignores_young_and_illiquid_coins():
    cfg = XSConfig(universe_size=15)
    bars = market()
    bars["NEWUSDT"] = coin(0.05, n=50, volume=1e9, start="2022-05-01")     # 40 days old on the test day
    bars["C0USDT"]["quote_volume"] = 1.0                                   # hardly traded
    p = panels(bars, None, cfg)
    d = pd.Timestamp("2022-06-10", tz="UTC")
    longs, shorts = pick(p["score"].loc[d], p["liquidity"].loc[d], p["age"].loc[d], p["close"].loc[d], cfg)
    assert "NEWUSDT" not in longs + shorts and "C0USDT" not in longs + shorts


def test_panels_never_use_future_data():
    cfg = XSConfig()
    bars = market()
    cut = pd.Timestamp("2022-05-01", tz="UTC")
    changed = {s: d.assign(close=d["close"].where(d.index <= cut, d["close"] * 5)) for s, d in bars.items()}
    a, b = panels(bars, None, cfg), panels(changed, None, cfg)
    for name in ("score", "vol", "liquidity", "age"):
        pd.testing.assert_frame_equal(a[name].loc[:cut], b[name].loc[:cut])


def test_delisted_coin_is_closed_at_a_penalty():
    bars = {"DEADUSDT": coin(0, n=10), "LIVEUSDT": coin(0, n=20, seed=1)}

    def strategy(symbol, history, state):
        return Target(1.0) if symbol == "DEADUSDT" and state.positions[symbol].qty == 0 and len(history) == 2 else None
    res = Backtester(BacktestConfig(slippage_bps={}, taker_fee=0.0), bars).run(strategy)
    last = res.fills[res.fills["reason"] == "delisted"]
    assert len(last) == 1 and last["price"].iloc[0] == pytest.approx(bars["DEADUSDT"]["close"].iloc[-1] * 0.98)


def test_only_rebalances_on_sunday_and_kill_switch_flattens():
    cfg, risk, _ = load_xs_config()
    bars = market()
    s = XSMomentum(bars, cfg, risk)
    positions = {k: Position() for k in bars}
    idx = s.p["close"].index
    monday = next(d for d in idx[120:] if d.weekday() == 0)
    sunday = monday + pd.Timedelta(days=6)
    assert s.targets({}, PortfolioState(20_000, positions, monday)) == {}
    out = s.targets({}, PortfolioState(20_000, positions, sunday))
    assert len(out) == 8
    assert sum(t.qty > 0 for t in out.values()) == 4 and all(t.stop is not None for t in out.values())
    held = {k: Position(qty=1.0, entry_price=100, stop=90) if k == "C1USDT" else Position() for k in bars}
    out = s.targets({}, PortfolioState(20_000 * 0.6, held, sunday + pd.Timedelta(days=1)))
    assert out["C1USDT"].qty == 0 and s.breakers.stopped


def test_positions_stay_inside_their_risk_limits():
    cfg, risk, _ = load_xs_config()
    bars = market()
    s = XSMomentum(bars, cfg, risk)
    positions = {k: Position() for k in bars}
    sunday = next(d for d in s.p["close"].index[120:] if d.weekday() == 6)
    out = s.targets({}, PortfolioState(20_000, positions, sunday))
    for sym, t in out.items():
        price = s.p["close"].at[sunday, sym]
        assert abs(t.qty) * abs(price - t.stop) <= 20_000 * risk.max_position_risk + 1e-6
        assert abs(price - t.stop) / price <= cfg.max_stop + 1e-9
