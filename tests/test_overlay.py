"""Regime overlay + funding layer (spec step 5)."""

import math

import numpy as np
import pandas as pd
import pytest

from cryptobot.risk.overlay import OverlayConfig, funding_daily, market_state, overlay, regime_frame

CFG = OverlayConfig()


def row(**kw):
    base = dict(market=1.0, er=0.4, adx=30, vol_pct=0.5, funding_ann=0.10, funding_z=0.0)
    return pd.Series(base | kw)


def test_risk_on_trending_normal_long_is_full_size():
    d = overlay(row(), side=1, signal=3 / 7, cfg=CFG)
    assert d.multiplier == 1.0 and not d.block_increase and d.reasons == ()


def test_market_state_multipliers():
    assert overlay(row(market=-1.0), 1, 0.5, CFG).multiplier == 0.5      # long in risk-off
    assert overlay(row(market=-1.0), -1, -0.5, CFG).multiplier == 1.0    # short in risk-off
    assert overlay(row(market=1.0), -1, -0.5, CFG).multiplier == 0.5     # short in risk-on
    assert overlay(row(market=0.0), 1, 0.5, CFG).multiplier == 0.5       # mixed


def test_chop_and_high_vol_combine():
    d = overlay(row(er=0.1, adx=15, vol_pct=0.95), 1, 0.5, CFG)
    assert d.multiplier == pytest.approx(0.25) and len(d.reasons) == 2


def test_chop_ignored_when_one_measure_says_trend():
    assert overlay(row(er=0.1, adx=30), 1, 0.5, CFG).multiplier == 1.0


def test_funding_against_long_halves_then_closes():
    assert overlay(row(funding_ann=0.40), 1, 0.5, CFG).multiplier == 0.5
    assert overlay(row(funding_ann=0.70), 1, 0.5, CFG).multiplier == 0.0
    assert overlay(row(funding_ann=0.70), 1, 1.0, CFG).multiplier == 0.5      # full signal: keep half


def test_positive_funding_helps_shorts():
    # longs pay when funding is positive -> a short RECEIVES it: no penalty
    assert overlay(row(market=-1.0, funding_ann=0.70), -1, -0.5, CFG).multiplier == 1.0
    # negative funding: shorts pay
    assert overlay(row(market=-1.0, funding_ann=-0.40), -1, -0.5, CFG).multiplier == 0.5


def test_crowding_blocks_new_longs_only():
    assert overlay(row(funding_z=2.5), 1, 0.5, CFG).block_increase
    assert not overlay(row(funding_z=2.5), -1, -0.5, CFG).block_increase


def test_overlay_disabled_changes_nothing():
    assert overlay(row(market=-1.0, er=0.05, funding_ann=0.9), 1, 0.5, OverlayConfig(enabled=False)).multiplier == 1


def test_overlay_config_validation():
    from cryptobot.risk.overlay import validate_overlay
    validate_overlay(CFG)
    for bad in (dict(vol_high_mult=2.0), dict(chop_mult=-0.1), dict(funding_halve_ann=0.7, funding_exit_ann=0.6),
                dict(er_chop=0.4)):
        with pytest.raises(ValueError):
            validate_overlay(OverlayConfig(**bad))


def test_missing_funding_is_flagged_not_hidden():
    out = funding_daily(None, pd.date_range("2024-01-01", periods=3, tz="UTC"))
    assert out["funding_missing"].all() and (out["funding_ann"] == 0).all()


def test_market_state_up_and_down():
    idx = pd.date_range("2023-01-01", periods=300, tz="UTC")
    up = pd.DataFrame({"close": np.linspace(100, 300, 300)}, index=idx)
    down = pd.DataFrame({"close": np.linspace(300, 100, 300)}, index=idx)
    assert market_state(up, CFG).iloc[-1] == 1 and market_state(down, CFG).iloc[-1] == -1
    assert market_state(up, CFG).iloc[100] == 0          # no 200-day average yet


def test_funding_daily_sums_settlements_and_annualises():
    idx = pd.to_datetime(["2024-01-01 00:00", "2024-01-01 08:00", "2024-01-01 16:00"], utc=True)
    fund = pd.DataFrame({"interval_hours": 8, "rate": [0.0001, 0.0001, 0.0001]}, index=idx)
    out = funding_daily(fund, pd.date_range("2024-01-01", periods=2, tz="UTC"))
    assert out["funding_ann"].iloc[0] == pytest.approx(0.0003 * 365)       # ~11%/yr
    assert out["funding_ann"].iloc[1] == 0.0                              # no data -> neutral


def test_regime_frame_has_no_look_ahead():
    rng = np.random.default_rng(3)
    idx = pd.date_range("2022-01-01", periods=700, tz="UTC")
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.03, 700)))
    bars = pd.DataFrame({"open": c, "high": c * 1.02, "low": c * 0.98, "close": c, "volume": 1.0}, index=idx)
    f_idx = pd.date_range("2022-01-01", periods=700 * 3, freq="8h", tz="UTC")
    fund = pd.DataFrame({"interval_hours": 8, "rate": rng.normal(0.0001, 0.0002, len(f_idx))}, index=f_idx)
    full = regime_frame(bars, bars, fund, CFG)
    cut = idx[500]
    part = regime_frame(bars.loc[:cut], bars.loc[:cut], fund.loc[:cut + pd.Timedelta(hours=23)], CFG)
    pd.testing.assert_frame_equal(full.loc[:cut], part)
