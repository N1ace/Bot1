"""Regime overlay + funding layer (strategy-risk-spec.md layer 5 and section 4).

Adjusts how MUCH to hold, never which strategy runs. For each symbol and day a
size multiplier is computed from data up to that day's close:

  market state (BTC vs 200-day SMA + its 20-day slope)
      risk-on (above & rising):   longs x1,   shorts x0.5
      risk-off (below & falling): longs x0.5, shorts x1
      mixed:                      both x0.5
  trend vs chop: ER(20) < 0.15 or ADX(14) < 18 -> x0.5 (unless ER > 0.30 or ADX > 25)
  volatility: 30-day vol above the 90th percentile of the last year -> x0.5
  funding against the position (annualised, last 24h of settlements):
      > 30%/yr -> x0.5;  > 60%/yr -> x0 (close) unless the signal is at full strength (then x0.5)
  crowding: funding z-score (90 days) > 2 -> no NEW or bigger longs
      (the spec also wants "open interest rising fast"; OI history is only kept
      30 days by Binance, so the backtest uses funding alone - stricter, not looser)

Multipliers combine by multiplication, so a risk-off, choppy, high-vol day for a long
gives 0.5 x 0.5 x 0.5 = 0.125 of the normal size.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from cryptobot import indicators


@dataclass(frozen=True)
class OverlayConfig:
    enabled: bool = True
    btc_sma: int = 200
    sma_slope_days: int = 20
    er_n: int = 20
    er_trend: float = 0.30
    er_chop: float = 0.15
    adx_n: int = 14
    adx_trend: float = 25
    adx_chop: float = 18
    chop_mult: float = 0.5
    vol_window: int = 30
    vol_pct_window_days: int = 365
    vol_high_pct: float = 0.90
    vol_high_mult: float = 0.5
    funding_halve_ann: float = 0.30
    funding_exit_ann: float = 0.60
    funding_z_block: float = 2.0
    funding_z_days: int = 90


def market_state(btc: pd.DataFrame, cfg: OverlayConfig) -> pd.Series:
    """+1 risk-on, -1 risk-off, 0 mixed (NaN-safe: 0 until the 200-day average exists)."""
    sma = indicators.sma(btc, cfg.btc_sma)
    above = btc["close"] > sma
    rising = sma > sma.shift(cfg.sma_slope_days)
    state = pd.Series(0, index=btc.index, dtype=float)
    state[above & rising] = 1.0
    state[~above & ~rising & sma.notna() & sma.shift(cfg.sma_slope_days).notna()] = -1.0
    return state


def validate_overlay(cfg: OverlayConfig) -> None:
    """The overlay may only SHRINK positions: every multiplier must be between 0 and 1."""
    for name in ("chop_mult", "vol_high_mult"):
        if not 0 <= getattr(cfg, name) <= 1:
            raise ValueError(f"overlay {name} must be between 0 and 1, got {getattr(cfg, name)}")
    if not 0 < cfg.funding_halve_ann <= cfg.funding_exit_ann:
        raise ValueError("funding: need 0 < halve_above_ann <= exit_above_ann")
    if not cfg.er_chop < cfg.er_trend or not cfg.adx_chop < cfg.adx_trend:
        raise ValueError("regime: chop thresholds must be below trend thresholds")


def funding_daily(funding: pd.DataFrame, index: pd.DatetimeIndex, z_days: int = 90) -> pd.DataFrame:
    """Per day (settlements during that UTC day): annualised rate, z-score of the daily sum over
    the previous `z_days`, and `funding_missing` = True where there was no data (treated as 0)."""
    if funding is None or funding.empty:
        return pd.DataFrame({"funding_ann": 0.0, "funding_z": 0.0, "funding_missing": True}, index=index)
    rates = funding["rate"]
    # Sum of all settlements in the day (works for 8h, 4h or 1h intervals) x 365 = cost per year.
    daily_sum = rates.groupby(rates.index.floor("D")).sum()
    ann = daily_sum * 365
    hist = daily_sum.shift(1).rolling(z_days, min_periods=30)
    z = (daily_sum - hist.mean()) / hist.std()
    out = pd.DataFrame({"funding_ann": ann, "funding_z": z}).reindex(index)
    out["funding_missing"] = out["funding_ann"].isna()
    return out.fillna({"funding_ann": 0.0, "funding_z": 0.0})


def regime_frame(bars: pd.DataFrame, btc: pd.DataFrame, funding: pd.DataFrame | None,
                 cfg: OverlayConfig) -> pd.DataFrame:
    """Everything the overlay needs, per day, computed only from the past."""
    out = pd.DataFrame(index=bars.index)
    out["market"] = market_state(btc, cfg).reindex(bars.index).fillna(0.0)
    out["er"] = indicators.efficiency_ratio(bars, cfg.er_n)
    out["adx"] = indicators.adx(bars, cfg.adx_n)
    vol = np.log(bars["close"]).diff().rolling(cfg.vol_window).std() * math.sqrt(365)
    out["vol_pct"] = vol.rolling(cfg.vol_pct_window_days, min_periods=120).rank(pct=True)
    out = out.join(funding_daily(funding, bars.index, cfg.funding_z_days))
    return out


@dataclass(frozen=True)
class OverlayDecision:
    multiplier: float
    block_increase: bool          # crowding: may hold or reduce, not add
    reasons: tuple[str, ...]


def overlay(row: pd.Series, side: float, signal: float, cfg: OverlayConfig) -> OverlayDecision:
    """Size multiplier for a position on `side` (+1 long / -1 short) given today's regime row."""
    if not cfg.enabled or side == 0:
        return OverlayDecision(1.0, False, ())
    mult, reasons = 1.0, []
    state = row["market"]
    if (side > 0 and state < 0) or (side < 0 and state > 0) or state == 0:
        mult *= 0.5
        reasons.append({1.0: "risk-on market (shorts x0.5)", -1.0: "risk-off market (longs x0.5)",
                        0.0: "mixed market x0.5"}[state])
    er, adx = row["er"], row["adx"]
    trending = (not math.isnan(er) and er > cfg.er_trend) or (not math.isnan(adx) and adx > cfg.adx_trend)
    choppy = (not math.isnan(er) and er < cfg.er_chop) or (not math.isnan(adx) and adx < cfg.adx_chop)
    if choppy and not trending:
        mult *= cfg.chop_mult
        reasons.append(f"chop x{cfg.chop_mult}")
    if not math.isnan(row["vol_pct"]) and row["vol_pct"] > cfg.vol_high_pct:
        mult *= cfg.vol_high_mult
        reasons.append(f"high volatility x{cfg.vol_high_mult}")
    against = row["funding_ann"] * side          # positive = this position PAYS funding
    if against > cfg.funding_exit_ann:
        full = abs(signal) >= 1.0 - 1e-9
        mult *= 0.5 if full else 0.0
        reasons.append(f"funding {against:.0%}/yr against position -> {'x0.5 (full signal)' if full else 'close'}")
    elif against > cfg.funding_halve_ann:
        mult *= 0.5
        reasons.append(f"funding {against:.0%}/yr against position x0.5")
    block = side > 0 and row["funding_z"] > cfg.funding_z_block
    if block:
        reasons.append(f"crowded (funding z {row['funding_z']:.1f}): no new longs")
    return OverlayDecision(mult, block, tuple(reasons))
