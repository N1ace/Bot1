"""Satellite sleeve: a 4-hour swing trader whose tactic depends on the daily regime.

The core (donchian_portfolio.py) earns in big trends and slowly bleeds in sideways
markets. The satellite is built to earn in exactly those other weeks:

  daily regime (per coin, decided from the PREVIOUS full day - never today's unfinished one)
    trend up / trend down  ER(20) > 0.30 or ADX(14) > 25, direction = close vs EMA(50)
                           and the EMA's 10-day slope agreeing
    chop                   ER(20) < 0.15 or ADX(14) < 18 (and not trending)
    in between             no new trades, open ones are managed to their exit

  tactic A - PULLBACK in a trend (4h bars):
    entry  trend up: price dipped below EMA(20) on the previous 4h bar and closes back
           above it now (the dip is over) -> long. Mirror image for trend down -> short.
    stop   2 x ATR(14, 4h) from entry, then trailed at 3 x ATR from the close (never loosened)
    exit   trailing stop, or the daily trend direction flips

  tactic B - MEAN REVERSION in chop (4h bars):
    entry  close below the lower Bollinger band (20, 2 std) and RSI(14) < 30 -> long;
           above the upper band and RSI > 70 -> short (stretched too far, expect a snap back)
    stop   1.5 x ATR(14, 4h) beyond the entry (fixed)
    exit   close back at the middle band (the 20-bar average), or after 6 bars (24 hours)
           without getting there - the snap-back didn't come, so get out

Sizing and risk (same layers as the core, on the satellite's own capital):
  qty = equity x r / (stop distance x (1 + slip buffer)), r = 1% per trade,
  then the portfolio caps (heat, cluster, leverage, net exposure), circuit breakers
  (daily -5% / weekly -10% block new trades, -15% / -25% drawdown shrink size,
  -35% = KILL SWITCH: flatten and stop until the owner reviews), losing-streak brake.
  Funding layer: no new trade when funding against it costs more than 30%/yr.

Signals are taken at a 4h close and filled at the next 4h open by the backtester
(no look-ahead). Every indicator only looks backwards.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from cryptobot import indicators
from cryptobot.backtest.engine import PortfolioState, Target
from cryptobot.risk.portfolio import CircuitBreakers, RiskConfig, apply_portfolio_caps, validate_hard_limits

BARS_PER_DAY = 6


@dataclass(frozen=True)
class SatelliteConfig:
    # daily regime
    er_n: int = 20
    er_trend: float = 0.30
    er_chop: float = 0.15
    adx_n: int = 14
    adx_trend: float = 25
    adx_chop: float = 18
    trend_ema: int = 50
    trend_slope_days: int = 10
    # tactic A: pullback
    pullback_ema: int = 20
    atr_n: int = 14
    trend_stop_atr: float = 2.0
    trend_trail_atr: float = 3.0
    # tactic B: mean reversion
    bb_n: int = 20
    bb_std: float = 2.0
    rsi_n: int = 14
    rsi_low: float = 30
    rsi_high: float = 70
    mr_stop_atr: float = 1.5
    mr_max_bars: int = 6
    # funding layer
    funding_block_ann: float = 0.30
    tactics: tuple[str, ...] = ("pullback", "meanrev")
    vol_window_days: int = 60


def load_satellite_config(path: str | Path = "config/satellite.yaml") -> tuple[SatelliteConfig, RiskConfig, dict]:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    s, r = raw["strategy"], raw["risk"]
    sat = SatelliteConfig(**{k: (tuple(v) if isinstance(v, list) else v) for k, v in s.items()
                             if k in SatelliteConfig.__dataclass_fields__})
    risk = RiskConfig(
        risk_per_trade=float(r["risk_per_trade"]), vol_target_asset=1.0,   # sized by stop distance only
        k_atr=sat.trend_stop_atr, slip_buffer=dict(r["slip_buffer"]),
        isolated_leverage=int(r["isolated_leverage"]), stop_to_liq_max=float(r["stop_to_liq_max"]),
        max_eff_leverage=float(r["max_eff_leverage"]), max_positions=int(r["max_positions"]),
        max_heat=float(r["max_heat"]), cluster=tuple(r["cluster"]), cluster_heat=float(r["cluster_heat"]),
        corr_floor=float(r["corr_floor"]), max_net_notional=float(r["max_net_notional"]),
        daily_loss=float(r["daily_loss"]), weekly_loss=float(r["weekly_loss"]),
        dd_steps=tuple(sorted((float(k), float(v)) for k, v in r["dd_steps"].items())),
        dd_stop=float(r["dd_stop"]), losing_streak_count=int(r["losing_streak"]["count"]),
        losing_streak_mult=float(r["losing_streak"]["mult"]), losing_streak_trades=int(r["losing_streak"]["trades"]),
    )
    validate_hard_limits(risk, raw.get("risk_profile", "conservative"))
    return sat, risk, raw


def daily_regime(daily: pd.DataFrame, cfg: SatelliteConfig) -> pd.DataFrame:
    """Per day: regime ('up', 'down', 'chop' or '') computed from that day's close."""
    er = indicators.efficiency_ratio(daily, cfg.er_n)
    adx = indicators.adx(daily, cfg.adx_n)
    ema = daily["close"].ewm(span=cfg.trend_ema, adjust=False).mean()
    ema[: cfg.trend_ema] = np.nan
    trending = (er > cfg.er_trend) | (adx > cfg.adx_trend)
    choppy = ((er < cfg.er_chop) | (adx < cfg.adx_chop)) & ~trending
    up = trending & (daily["close"] > ema) & (ema > ema.shift(cfg.trend_slope_days))
    down = trending & (daily["close"] < ema) & (ema < ema.shift(cfg.trend_slope_days))
    regime = pd.Series("", index=daily.index, dtype=object)
    regime[choppy] = "chop"
    regime[up] = "up"
    regime[down] = "down"
    regime[er.isna() | adx.isna() | ema.isna()] = ""
    return pd.DataFrame({"regime": regime})


def to_daily(bars: pd.DataFrame) -> pd.DataFrame:
    """4h bars -> daily bars (UTC days). Only complete days (6 bars) are kept."""
    g = bars.groupby(bars.index.floor("D"))
    daily = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                          "close": g["close"].last(), "volume": g["volume"].sum()})
    return daily[g.size() == BARS_PER_DAY]


def precompute(bars: pd.DataFrame, funding: pd.DataFrame | None, cfg: SatelliteConfig) -> pd.DataFrame:
    """Per 4h bar: indicators + the regime of the last COMPLETE day before this bar closes."""
    out = pd.DataFrame(index=bars.index)
    close = bars["close"]
    out["close"] = close
    ema = close.ewm(span=cfg.pullback_ema, adjust=False).mean()
    ema[: cfg.pullback_ema] = np.nan
    out["ema"] = ema
    out["atr"] = indicators.atr(bars, cfg.atr_n)
    mid = close.rolling(cfg.bb_n).mean()
    std = close.rolling(cfg.bb_n).std()
    out["bb_mid"], out["bb_low"], out["bb_high"] = mid, mid - cfg.bb_std * std, mid + cfg.bb_std * std
    out["rsi"] = indicators.rsi(bars, cfg.rsi_n)
    # Day D's regime is known at D+1 00:00, so it applies to bars that open on D+1.
    reg = daily_regime(to_daily(bars), cfg)["regime"]
    reg.index = reg.index + pd.Timedelta(days=1)
    out["regime"] = reg.reindex(bars.index.floor("D")).fillna("").values
    # Funding: annualised rate of the latest settlement at or before this bar's close.
    if funding is not None and not funding.empty:
        rate = funding["rate"].groupby(level=0).last()
        per_year = 365 * 24 / funding.get("interval_hours", pd.Series(8, index=funding.index)).fillna(8)
        ann = (rate * per_year.groupby(level=0).last()).sort_index()
        closes = bars.index + pd.Timedelta(hours=4)
        out["funding_ann"] = ann.reindex(ann.index.union(closes)).ffill().reindex(closes).fillna(0.0).values
    else:
        out["funding_ann"] = 0.0
    return out


@dataclass
class _Trade:
    tactic: str            # 'pullback' or 'meanrev'
    side: float            # +1 long / -1 short
    stop: float
    bars_held: int = 0


@dataclass
class SatellitePortfolio:
    bars: dict[str, pd.DataFrame]
    cfg: SatelliteConfig
    risk: RiskConfig
    funding: dict[str, pd.DataFrame] | None = None
    breaker_state: dict | None = None
    trades: dict[str, _Trade] = field(default_factory=dict)
    log: list[dict] = field(default_factory=list)

    def __post_init__(self):
        self.data = {s: precompute(df, (self.funding or {}).get(s), self.cfg) for s, df in self.bars.items()}
        daily_close = pd.DataFrame({s: to_daily(df)["close"] for s, df in self.bars.items()})
        rets = np.log(daily_close).diff()
        names = list(rets)
        self.corr = {(a, b): rets[a].rolling(self.cfg.vol_window_days).corr(rets[b])
                     for i, a in enumerate(names) for b in names[i + 1:]}
        self.breakers = CircuitBreakers.from_state(self.risk, self.breaker_state)
        self._day = None

    # ---- entry / exit rules (one symbol, one bar) --------------------------------------

    def _entry(self, s: str, i: int) -> tuple[str, float, float] | None:
        """(tactic, side, stop distance) for a new trade at bar i, or None."""
        d = self.data[s]
        row, prev = d.iloc[i], d.iloc[i - 1] if i > 0 else None
        if prev is None or any(map(math.isnan, (row["atr"], row["ema"], row["bb_mid"], row["rsi"]))):
            return None
        regime, c = row["regime"], self.cfg
        if "pullback" in c.tactics and regime in ("up", "down"):
            side = 1.0 if regime == "up" else -1.0
            dipped = (prev["close"] - prev["ema"]) * side < 0
            back = (row["close"] - row["ema"]) * side > 0
            if dipped and back:
                return "pullback", side, c.trend_stop_atr * row["atr"]
        if "meanrev" in c.tactics and regime == "chop":
            if row["close"] < row["bb_low"] and row["rsi"] < c.rsi_low:
                return "meanrev", 1.0, c.mr_stop_atr * row["atr"]
            if row["close"] > row["bb_high"] and row["rsi"] > c.rsi_high:
                return "meanrev", -1.0, c.mr_stop_atr * row["atr"]
        return None

    def _should_exit(self, s: str, i: int, t: _Trade) -> bool:
        row = self.data[s].iloc[i]
        if t.tactic == "pullback":
            flipped = row["regime"] in ("up", "down") and (1.0 if row["regime"] == "up" else -1.0) != t.side
            return flipped
        reached_mid = (row["close"] - row["bb_mid"]) * t.side >= 0
        return reached_mid or t.bars_held >= self.cfg.mr_max_bars

    def _trail(self, s: str, i: int, t: _Trade) -> float:
        if t.tactic != "pullback":
            return t.stop
        row = self.data[s].iloc[i]
        fresh = row["close"] - t.side * self.cfg.trend_trail_atr * row["atr"]
        if not math.isfinite(fresh):
            return t.stop
        return max(t.stop, fresh) if t.side > 0 else min(t.stop, fresh)

    def _room_for_new(self, equity, held, new, corr) -> float:
        """Largest fraction (0-1) of the new trades that fits under every portfolio cap
        WITHOUT shrinking positions that are already open (they keep their size and stop)."""
        def fits(f):
            wanted = {**held, **{s: (v[0] * f, v[1], v[2]) for s, v in new.items()}}
            capped = apply_portfolio_caps(equity, wanted, corr, self.risk)
            return all(abs(capped[s] - wanted[s][0]) <= 1e-9 * max(1.0, abs(wanted[s][0])) for s in wanted)
        if fits(1.0):
            return 1.0
        lo, hi = 0.0, 1.0
        for _ in range(30):                        # bisection: caps only ever get tighter as f grows
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if fits(mid) else (lo, mid)
        return lo

    # ---- the portfolio step the backtester calls at every 4h close ------------------------

    def targets(self, histories: dict[str, pd.DataFrame], state: PortfolioState) -> dict[str, Target | None]:
        date, equity = state.date, state.equity
        for _, pnl in state.closed_trades:
            self.breakers.record_trade(pnl)
        day = date.floor("D")
        if day != self._day:                       # daily/weekly limits + drawdown steps: once a day
            self.breakers.update(day, equity)
            self._day = day
        b = self.breakers
        b.high_water = max(b.high_water, equity)
        if not b.stopped and equity <= b.high_water * (1 - self.risk.dd_stop):
            b.stopped = True                       # kill switch checked at every bar, not just daily
            b.events.append((date, f"drawdown {1 - equity / b.high_water:.1%}: FLATTEN AND STOP"))
        if b.stopped:
            self.trades.clear()
            return {s: Target(0.0) for s, p in state.positions.items() if p.qty != 0}

        out: dict[str, Target | None] = {}
        r = self.risk.risk_per_trade * b.r_multiplier()
        blocked = b.entries_blocked(day)
        new: dict[str, tuple[float, float, float]] = {}
        held: dict[str, tuple[float, float, float]] = {}
        for s, hist in histories.items():
            pos, d = state.positions[s], self.data[s]
            i = d.index.get_loc(date)
            t = self.trades.get(s)
            if t is not None and pos.qty == 0:     # stopped out by the exchange stop
                del self.trades[s]
                t = None
            if t is not None:
                t.bars_held += 1
                if self._should_exit(s, i, t):
                    del self.trades[s]
                    out[s] = Target(0.0)
                    self.log.append({"date": date, "symbol": s, "action": "exit", "tactic": t.tactic})
                    continue
                t.stop = self._trail(s, i, t)
                price = d.iloc[i]["close"]
                held[s] = (pos.qty, abs(price - t.stop), price)
                out[s] = Target(pos.qty, t.stop) if t.stop != pos.stop else None
                continue
            if blocked or r == 0:
                continue
            entry = self._entry(s, i)
            if entry is None:
                continue
            tactic, side, dist = entry
            if d.iloc[i]["funding_ann"] * side > self.cfg.funding_block_ann:
                continue                           # funding layer: too expensive to hold this side
            price = d.iloc[i]["close"]
            if dist / price > self.risk.stop_to_liq_max * self.risk.liq_distance:
                continue                           # stop would sit too close to liquidation
            qty = side * equity * r / (dist * (1 + self.risk.slip(s)))
            new[s] = (qty, dist, price, tactic)
        if new:
            corr_today = {}
            for k, v in self.corr.items():
                val = v.reindex([day - pd.Timedelta(days=1)]).iloc[0]   # yesterday's complete day
                corr_today[k] = 1.0 if math.isnan(val) else val
            scale = self._room_for_new(equity, held, new, corr_today)
            capped = {s: v[0] * scale for s, v in new.items()}
            for s, (_, dist, price, tactic) in new.items():
                qty = capped[s]
                if qty == 0:
                    continue
                side = math.copysign(1, qty)
                stop = price - side * dist
                self.trades[s] = _Trade(tactic, side, stop)
                out[s] = Target(qty, stop)
                self.log.append({"date": date, "symbol": s, "action": "entry", "tactic": tactic,
                                 "side": side, "qty": qty, "stop": stop, "r": r})
        return out
