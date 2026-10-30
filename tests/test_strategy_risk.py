"""Donchian ensemble (step 3) and portfolio risk layers 1-4 (step 4)."""

import dataclasses
import math

import numpy as np
import pandas as pd
import pytest

from cryptobot.backtest.engine import Backtester, BacktestConfig, PortfolioState, Position
from cryptobot.risk.portfolio import (CircuitBreakers, RiskConfig, SymbolInput, apply_portfolio_caps, cluster_risk,
                                      size_position)
from cryptobot.strategies.donchian import ensemble_signal, sub_signal
from cryptobot.strategies.donchian_portfolio import DonchianPortfolio, StrategyConfig, precompute


def series(values):
    return pd.Series(values, index=pd.date_range("2024-01-01", periods=len(values), tz="UTC"), dtype=float)


def ohlc(closes):
    c = series(closes)
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1.0})


# ---- step 3: strategy ------------------------------------------------------------

def test_sub_signal_enters_on_breakout_and_exits_below_midpoint():
    #            warm-up (3 days)  breakout  hold   drop below mid of prev 3 closes
    s = sub_signal(series([10, 11, 10, 12, 13, 11]), lookback=3)
    assert list(s) == [0, 0, 0, 1, 1, 0]   # day3: 12 > max(10,11,10); day5: 11 < mid(11..13)=12


def test_trailing_stop_only_moves_up():
    closes = [10, 10, 10, 11, 12, 13, 14, 13.4]   # stop ratchets to mid(12,13,14)=13 ... 13.4 > 13 stays long
    assert sub_signal(series(closes), 3).iloc[-1] == 1


def test_no_shorts_unless_enabled():
    falling = series([10, 10, 10, 9, 8, 7])
    assert (sub_signal(falling, 3) == 0).all()
    assert sub_signal(falling, 3, allow_shorts=True).iloc[3] == -1


def test_ensemble_is_average_of_sub_signals():
    closes = series(list(np.linspace(100, 200, 300)))      # steady uptrend: all sub-signals long
    assert ensemble_signal(closes).iloc[-1] == 1.0
    assert ensemble_signal(closes).iloc[15] == pytest.approx(1 / 7)   # only L=10 has enough history


def test_precompute_has_no_look_ahead():
    rng = np.random.default_rng(1)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.03, 400)))
    full = precompute(ohlc(closes), StrategyConfig())
    prefix = precompute(ohlc(closes[:300]), StrategyConfig())
    pd.testing.assert_frame_equal(full.iloc[:300], prefix)


# ---- step 4: layer 1 + 2 sizing -----------------------------------------------------

CFG = RiskConfig()


def test_size_position_formula():
    # stop = 3 x ATR 3 = 9; risk qty = 20000 x 0.005 / (9 x 1.25) = 8.889; vol qty = 20000 x 0.15 / (0.6 x 100) = 50
    qty, stop = size_position(20_000, "BTCUSDT", SymbolInput(price=100, atr=3, vol_ann=0.6, signal=1.0), CFG, 0.005)
    assert stop == 9 and qty == pytest.approx(8.8889, rel=1e-3)


def test_size_scales_with_signal_and_vol_cap():
    half, _ = size_position(20_000, "BTCUSDT", SymbolInput(100, 3, 0.6, 3 / 7), CFG, 0.005)
    assert half == pytest.approx(8.8889 * 3 / 7, rel=1e-3)
    # very volatile asset: the volatility cap is smaller than the risk size
    capped, _ = size_position(20_000, "BTCUSDT", SymbolInput(100, 0.1, 10.0, 1.0), CFG, 0.005)
    assert capped == pytest.approx(20_000 * 0.15 / (10.0 * 100))


def test_stop_too_close_to_liquidation_rejects_trade():
    # 2x isolated: liquidation ~49.6% away; allowed stop <= 0.33 x 49.6% = 16.4% of price
    qty, _ = size_position(20_000, "BTCUSDT", SymbolInput(100, 6, 0.6, 1.0), CFG, 0.005)   # stop 18%
    assert qty == 0


def test_no_size_without_signal_or_history():
    assert size_position(20_000, "BTCUSDT", SymbolInput(100, 3, 0.6, 0.0), CFG, 0.005)[0] == 0
    assert size_position(20_000, "BTCUSDT", SymbolInput(100, math.nan, 0.6, 1.0), CFG, 0.005)[0] == 0


# ---- step 4: layer 2-3 portfolio caps -----------------------------------------------

def test_heat_cap_scales_positions_down():
    # each position risks 1.5% -> heat 3% > 2% -> scale by 2/3 (cluster cap off for this test)
    cfg = RiskConfig(cluster=(), max_eff_leverage=100, max_net_notional=100)
    capped = apply_portfolio_caps(10_000, {"A": (15, 10, 1), "B": (15, 10, 1)}, {}, cfg)
    assert capped["A"] == pytest.approx(10) and capped["B"] == pytest.approx(10)


def test_cluster_risk_uses_correlation_floor():
    # two 1% risks, correlation 0.2 floored to 0.7: sqrt(1+1+2*0.7)*1% = 1.84%
    assert cluster_risk({"BTCUSDT": 0.01, "ETHUSDT": 0.01}, {("BTCUSDT", "ETHUSDT"): 0.2}, 0.7) == \
        pytest.approx(math.sqrt(3.4) * 0.01)


def test_cluster_cap_applies_to_btc_eth():
    cfg = RiskConfig(max_heat=1.0, max_eff_leverage=100, max_net_notional=100)
    capped = apply_portfolio_caps(10_000, {"BTCUSDT": (10, 10, 1), "ETHUSDT": (10, 10, 1)},
                                  {("BTCUSDT", "ETHUSDT"): 0.9}, cfg)
    risks = {s: capped[s] * 10 / 10_000 for s in capped}
    assert cluster_risk(risks, {("BTCUSDT", "ETHUSDT"): 0.9}, 0.7) == pytest.approx(cfg.cluster_heat)


def test_effective_leverage_cap():
    cfg = RiskConfig(max_heat=1.0, cluster=(), max_net_notional=100)
    capped = apply_portfolio_caps(10_000, {"A": (300, 0.01, 100)}, {}, cfg)   # 30,000 notional = 3x
    assert capped["A"] * 100 == pytest.approx(15_000)                          # capped at 1.5x


# ---- step 4: layer 4 circuit breakers ------------------------------------------------

def day(n):
    return pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(days=n)   # 2024-01-01 is a Monday


def test_daily_loss_blocks_next_order_only():
    cb = CircuitBreakers(CFG)
    cb.update(day(0), 10_000)
    cb.update(day(1), 9_790)                  # -2.1% in a day
    assert cb.entries_blocked(day(1)) is True
    cb.update(day(2), 9_790)
    assert cb.entries_blocked(day(2)) is False


def test_weekly_loss_blocks_until_monday():
    cb = CircuitBreakers(RiskConfig(daily_loss=0.5))
    for n, eq in enumerate([10_000, 9_900, 9_800, 9_700, 9_590]):   # Mon..Fri, -4.1% this week
        cb.update(day(n), eq)
    assert cb.entries_blocked(day(5)) and cb.entries_blocked(day(6))
    cb.update(day(7), 9_590)                                           # next Monday
    assert not cb.entries_blocked(day(7))


def test_drawdown_steps_stop_and_recovery():
    cb = CircuitBreakers(RiskConfig(daily_loss=1, weekly_loss=1))
    cb.update(day(0), 10_000)
    cb.update(day(1), 8_900)                  # -11% -> half risk
    assert cb.r_multiplier() == 0.5
    cb.update(day(2), 8_400)                  # -16% -> quarter risk
    assert cb.r_multiplier() == 0.25
    cb.update(day(3), 9_300)                  # recovered more than half of 10k->8.4k: one step back up
    assert cb.r_multiplier() == 0.5
    cb.update(day(4), 7_900)                  # -21% -> flatten and stop
    assert cb.stopped and cb.r_multiplier() == 0 and cb.entries_blocked(day(30))


def test_losing_streak_halves_risk_for_ten_trades():
    cb = CircuitBreakers(CFG)
    for _ in range(6):
        cb.record_trade(-10)
    assert cb.r_multiplier() == 0.5
    for _ in range(10):
        cb.record_trade(+5)
    assert cb.r_multiplier() == 1.0


# ---- risk review fixes ------------------------------------------------------------------

def test_breaker_state_survives_restart_and_stop_is_sticky():
    from cryptobot.storage import TradeStore
    cb = CircuitBreakers(RiskConfig(daily_loss=1, weekly_loss=1))
    cb.update(day(0), 10_000)
    cb.update(day(1), 7_900)                                  # -21% -> stopped
    store = TradeStore(":memory:", "demo", "futures", "FUTURES")
    store.save_risk_state("donchian", cb.to_state())
    restored = CircuitBreakers.from_state(RiskConfig(daily_loss=1, weekly_loss=1), store.load_risk_state("donchian"))
    assert restored.stopped and restored.high_water == 10_000
    restored.update(day(2), 12_000)                           # even a new high doesn't un-stop it
    assert restored.stopped and restored.r_multiplier() == 0


def test_config_above_hard_limits_is_refused():
    from cryptobot.risk.portfolio import validate_hard_limits
    validate_hard_limits(RiskConfig())                        # spec defaults are fine
    for bad in (dict(max_heat=0.05), dict(isolated_leverage=5), dict(max_eff_leverage=3.0),
                dict(cluster_heat=0.02), dict(max_positions=8), dict(risk_per_trade=0.02)):
        with pytest.raises(ValueError):
            validate_hard_limits(RiskConfig(**bad))


def test_aggressive_profile_allows_the_trading_budget_but_keeps_the_caps():
    from cryptobot.risk.portfolio import validate_hard_limits
    budget = RiskConfig(risk_per_trade=0.015, max_heat=0.08, cluster_heat=0.05, max_positions=6,
                        max_eff_leverage=1.8, dd_steps=((0.15, 0.75), (0.25, 0.5)), dd_stop=0.35,
                        daily_loss=0.05, weekly_loss=0.10)
    with pytest.raises(ValueError):
        validate_hard_limits(budget)                          # too much for the conservative profile
    validate_hard_limits(budget, "aggressive")
    for bad in (dict(isolated_leverage=4), dict(dd_stop=0.5), dict(risk_per_trade=0.03),
                dict(max_eff_leverage=2.0),                   # 2x isolated can't margin 2x notional
                dict(dd_steps=((0.15, 0.75), (0.40, 0.5)))):  # a step after the kill switch
        with pytest.raises(ValueError):
            validate_hard_limits(dataclasses.replace(budget, **bad), "aggressive")
    with pytest.raises(ValueError):
        validate_hard_limits(budget, "yolo")


def test_repo_core_config_loads_with_aggressive_profile():
    from cryptobot.strategies.donchian_portfolio import load_strategy_config
    strat, risk, raw = load_strategy_config("config/core.yaml")
    assert raw["risk_profile"] == "aggressive" and strat.allow_shorts
    assert risk.isolated_leverage <= 3 and risk.dd_stop <= 0.40


def test_repo_strategy_config_loads_and_passes_hard_limits():
    from cryptobot.strategies.donchian_portfolio import load_strategy_config
    strat, risk, raw = load_strategy_config("config/strategy.yaml")
    assert risk.risk_per_trade == 0.005 and strat.lookbacks == (10, 20, 30, 60, 90, 150, 250)


def _state(qty, stop, equity=20_000, date=None, stopped=()):
    return PortfolioState(equity=equity, positions={"BTCUSDT": Position(qty=qty, entry_price=100, stop=stop)},
                          date=date, stopped_out=set(stopped))


def test_stop_trails_up_and_never_loosens():
    bars = {"BTCUSDT": trending_bars()}
    strat = DonchianPortfolio(bars, StrategyConfig(), RiskConfig())
    date = bars["BTCUSDT"].index[-1]
    hist = {"BTCUSDT": bars["BTCUSDT"].loc[:date]}
    price, atr = hist["BTCUSDT"]["close"].iloc[-1], strat.data["BTCUSDT"].loc[date, "atr"]
    low_stop = price - 10 * atr                               # far below: must be raised
    t = strat.targets(hist, _state(1.0, low_stop, date=date))["BTCUSDT"]
    assert t.stop == pytest.approx(price - 3 * atr)
    high_stop = price - 1 * atr                               # already tighter: must be kept
    t2 = strat.targets(hist, _state(1.0, high_stop, date=date))["BTCUSDT"]
    assert t2 is None or t2.stop == high_stop


def test_cap_reduction_is_not_skipped_by_rebalance_threshold():
    bars = {"BTCUSDT": trending_bars()}
    strat = DonchianPortfolio(bars, StrategyConfig(), RiskConfig(max_eff_leverage=0.01))   # tiny cap
    date = bars["BTCUSDT"].index[-1]
    price = bars["BTCUSDT"].loc[date, "close"]
    cur = 0.011 * 20_000 / price                              # 1.1% of equity: cap wants ~10% less
    t = strat.targets({"BTCUSDT": bars["BTCUSDT"].loc[:date]}, _state(cur, price * 0.9, date=date))["BTCUSDT"]
    assert t is not None and abs(t.qty) * price <= 0.01 * 20_000 + 1e-6


def test_short_reentry_block_after_stop():
    strat = DonchianPortfolio({"BTCUSDT": trending_bars()}, StrategyConfig(allow_shorts=True), RiskConfig())
    strat.stop_block["BTCUSDT"] = -0.5 * 3 / 7                # short stopped at 3/7 short
    assert strat._blocked_after_stop("BTCUSDT", -0.5 * 2 / 7)  # weaker short: stay out
    assert strat._blocked_after_stop("BTCUSDT", -0.5 * 3 / 7)  # same strength: stay out
    assert not strat._blocked_after_stop("BTCUSDT", -0.5 * 4 / 7)  # stronger: allowed


def test_flip_is_recorded_as_close_plus_open():
    from cryptobot.backtest.engine import Target
    data = ohlc([100, 100, 110, 110, 110])

    def flip(symbol, history, state):
        return {0: Target(1.0, stop=50), 1: Target(-1.0, stop=200)}.get(len(history) - 1)

    closed = []
    res = Backtester(BacktestConfig(initial_equity=10_000, taker_fee=0, slippage_bps={"default": 0}, qty_step={},
                                    min_notional={}), {"BTCUSDT": data}).run(
        lambda s, h, st: (closed.extend(st.closed_trades), flip(s, h, st))[1])
    assert list(res.fills["qty"]) == [1.0, -1.0, -1.0]      # open long, close long, open short
    assert closed and closed[0][1] == pytest.approx(10.0)    # the long's PnL was reported as a closed trade


# ---- integration: strategy + risk inside the engine ---------------------------------

def trending_bars(n=320, crash_at=None):
    closes = list(np.linspace(100, 200, n))
    if crash_at:
        closes[crash_at:] = [c * 0.6 for c in closes[crash_at:]]
    return ohlc(closes)


def test_portfolio_buys_uptrend_with_stop_below_price():
    bars = {"BTCUSDT": trending_bars()}
    strat = DonchianPortfolio(bars, StrategyConfig(), RiskConfig())
    res = Backtester(BacktestConfig(initial_equity=20_000, qty_step={}, min_notional={}), bars).run(strat)
    assert not res.fills.empty and res.fills.iloc[0]["qty"] > 0
    entry = [l for l in strat.log if l["target"] > 0][0]
    assert entry["stop"] < bars["BTCUSDT"].loc[entry["date"], "close"]


def test_portfolio_stops_out_in_crash_and_does_not_buy_straight_back():
    bars = {"BTCUSDT": trending_bars(crash_at=300)}
    strat = DonchianPortfolio(bars, StrategyConfig(), RiskConfig())
    res = Backtester(BacktestConfig(initial_equity=20_000, qty_step={}, min_notional={}), bars).run(strat)
    fills = res.fills.reset_index(drop=True)
    stop_rows = fills[fills["reason"].str.startswith("stop")]
    assert not stop_rows.empty
    stop_date = stop_rows.iloc[0]["date"]
    held_before = fills[fills["date"] < stop_date]["qty"].sum()
    after = fills[fills["date"] > stop_date]
    if not after.empty:
        # only a NEW breakout (after the signal reset) may re-enter: not within 10 days, and smaller
        assert after.iloc[0]["date"] >= stop_date + pd.Timedelta(days=10)
        assert after.iloc[0]["qty"] < held_before


def test_vol_target_sizing_ignores_stop_distance_until_the_ceiling():
    cfg = RiskConfig(sizing="vol_target", vol_target_asset=0.20, max_position_risk=0.03,
                     risk_per_trade=0.015, slip_buffer={"default": 0.0})
    inp = SymbolInput(price=100, atr=2, vol_ann=0.5, signal=1.0)          # stop 6% away
    qty, _ = size_position(10_000, "X", inp, cfg, cfg.risk_per_trade)
    assert qty == pytest.approx(10_000 * 0.20 / (0.5 * 100))              # 40 coins = $4,000
    assert qty * 6 / 10_000 <= 0.03                                       # stop hit costs 2.4%
    tight = dataclasses.replace(cfg, max_position_risk=0.01)
    qty, _ = size_position(10_000, "X", inp, tight, cfg.risk_per_trade)
    assert qty * 6 / 10_000 == pytest.approx(0.01)                        # ceiling takes over


def test_vol_target_sizing_still_obeys_circuit_breakers():
    cfg = RiskConfig(sizing="vol_target", vol_target_asset=0.20, max_position_risk=0.03,
                     risk_per_trade=0.015, slip_buffer={"default": 0.0})
    inp = SymbolInput(price=100, atr=2, vol_ann=0.5, signal=1.0)
    full, _ = size_position(10_000, "X", inp, cfg, cfg.risk_per_trade)
    half, _ = size_position(10_000, "X", inp, cfg, cfg.risk_per_trade * 0.5)
    none, _ = size_position(10_000, "X", inp, cfg, 0.0)
    assert half == pytest.approx(full / 2) and none == 0


def test_bigger_bets_config_stays_inside_the_aggressive_limits():
    from cryptobot.strategies.donchian_portfolio import load_strategy_config
    _, risk, raw = load_strategy_config("config/core_big.yaml")
    assert risk.sizing == "vol_target" and risk.max_position_risk <= 0.03 and risk.dd_stop == 0.35
    assert risk.isolated_leverage <= 3 and raw["risk_profile"] == "aggressive"
