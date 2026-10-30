"""Cross-sectional momentum: each week buy the strongest coins and short the weakest.

Each Sunday at the close (fills at Monday's open):
  1. universe: the `universe_size` most-traded coins by median USDT volume of the last
     30 days, at least 90 days old. Built only from data known that day, from a list
     that includes coins later delisted (backtest/universe.py).
  2. score = 28-day return / volatility over those 28 days.
  3. long the top 4, short the bottom 4.
  4. size each by volatility: notional = equity x position_vol / annual vol, so wild
     coins get smaller positions; a stop hit may cost at most max_position_risk.

Between Sundays nothing is traded; a coin stopped out sits out until the next Sunday.
Circuit breakers work as in the other sleeves: daily/weekly limits block new risk,
drawdown steps shrink it, the -35% kill switch flattens and stops.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from cryptobot import indicators
from cryptobot.backtest.engine import PortfolioState, Target
from cryptobot.risk.portfolio import CircuitBreakers, RiskConfig, apply_portfolio_caps, validate_hard_limits


@dataclass(frozen=True)
class XSConfig:
    universe_size: int = 30
    volume_days: int = 30
    min_universe: int = 15
    min_age_days: int = 90
    lookback_days: int = 28
    n_long: int = 4
    n_short: int = 4
    rebalance_weekday: int = 6
    rebalance_threshold: float = 0.20
    vol_days: int = 60
    vol_floor: float = 0.30
    position_vol: float = 0.05
    atr_n: int = 20
    k_atr: float = 3.0
    max_stop: float = 0.24
    funding_block_ann: float = 0.30


def load_xs_config(path: str | Path = "config/xs.yaml") -> tuple[XSConfig, RiskConfig, dict]:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    s, r = raw["strategy"], raw["risk"]
    cfg = XSConfig(**{k: v for k, v in s.items() if k in XSConfig.__dataclass_fields__})
    risk = RiskConfig(
        risk_per_trade=float(r["risk_per_trade"]), vol_target_asset=float(r["vol_target_asset"]), k_atr=cfg.k_atr,
        slip_buffer=dict(r["slip_buffer"]), isolated_leverage=int(r["isolated_leverage"]),
        stop_to_liq_max=float(r["stop_to_liq_max"]), max_eff_leverage=float(r["max_eff_leverage"]),
        max_positions=int(r["max_positions"]), max_heat=float(r["max_heat"]), cluster=tuple(r["cluster"]),
        cluster_heat=float(r["cluster_heat"]), corr_floor=float(r["corr_floor"]),
        max_net_notional=float(r["max_net_notional"]), daily_loss=float(r["daily_loss"]),
        weekly_loss=float(r["weekly_loss"]),
        dd_steps=tuple(sorted((float(k), float(v)) for k, v in r["dd_steps"].items())), dd_stop=float(r["dd_stop"]),
        losing_streak_count=int(r["losing_streak"]["count"]), losing_streak_mult=float(r["losing_streak"]["mult"]),
        losing_streak_trades=int(r["losing_streak"]["trades"]), sizing=str(r["sizing"]),
        max_position_risk=float(r["max_position_risk"]),
    )
    validate_hard_limits(risk, raw.get("risk_profile", "conservative"))
    return cfg, risk, raw


def panels(bars: dict[str, pd.DataFrame], funding: dict[str, pd.DataFrame] | None,
           cfg: XSConfig) -> dict[str, pd.DataFrame]:
    """Wide tables (one column per coin), each value using only data up to that day's close."""
    close = pd.DataFrame({s: d["close"] for s, d in bars.items()}).sort_index()
    log_ret = np.log(close).diff()
    window_vol = log_ret.rolling(cfg.lookback_days, min_periods=cfg.lookback_days).std() * math.sqrt(cfg.lookback_days)
    score = (close / close.shift(cfg.lookback_days) - 1) / window_vol
    vol = (log_ret.rolling(cfg.vol_days, min_periods=cfg.lookback_days).std() * math.sqrt(365)).clip(lower=cfg.vol_floor)
    volume = pd.DataFrame({s: d["quote_volume"] for s, d in bars.items()}).reindex(close.index)
    liquidity = volume.rolling(cfg.volume_days, min_periods=cfg.volume_days).median()
    age = close.notna().cumsum()
    atr = pd.DataFrame({s: indicators.atr(d, cfg.atr_n) for s, d in bars.items()}).reindex(close.index)
    fund = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    for s, f in (funding or {}).items():
        if s in fund and not f.empty:
            # annualised over the last 3 days' settlements, whatever the interval was
            daily = f["rate"].groupby(f.index.floor("D")).sum().reindex(close.index).fillna(0.0)
            fund[s] = daily.rolling(3, min_periods=1).mean() * 365
    return {"close": close, "score": score, "vol": vol, "liquidity": liquidity, "age": age, "atr": atr,
            "funding": fund}


def pick(row_score: pd.Series, row_liq: pd.Series, row_age: pd.Series, row_close: pd.Series,
         cfg: XSConfig) -> tuple[list[str], list[str]]:
    """(longs, shorts) for one day. Empty when too few coins qualify."""
    ok = row_close.notna() & row_score.notna() & row_liq.notna() & (row_age >= cfg.min_age_days)
    universe = row_liq[ok].sort_values(ascending=False).head(cfg.universe_size).index
    if len(universe) < cfg.min_universe:
        return [], []
    ranked = row_score[universe].sort_values(ascending=False)
    return list(ranked.index[:cfg.n_long]), list(ranked.index[-cfg.n_short:])


class XSMomentum:
    def __init__(self, bars: dict[str, pd.DataFrame], cfg: XSConfig, risk: RiskConfig,
                 funding: dict[str, pd.DataFrame] | None = None, breaker_state: dict | None = None):
        self.cfg, self.risk = cfg, risk
        self.p = panels(bars, funding, cfg)
        self.breakers = CircuitBreakers.from_state(risk, breaker_state)
        self.log: list[dict] = []

    def _stop_distance(self, s: str, date: pd.Timestamp, price: float) -> float:
        atr = self.p["atr"].at[date, s]
        return min(self.cfg.k_atr * atr, self.cfg.max_stop * price) if math.isfinite(atr) else math.nan

    def targets(self, histories: dict[str, pd.DataFrame], state: PortfolioState) -> dict[str, Target | None]:
        date, equity = state.date, state.equity
        for _, pnl in state.closed_trades:
            self.breakers.record_trade(pnl)
        self.breakers.update(date, equity)
        if self.breakers.stopped:
            return {s: Target(0.0) for s, p in state.positions.items() if p.qty != 0}
        if date.weekday() != self.cfg.rebalance_weekday:
            return {}
        p, cfg = self.p, self.cfg
        longs, shorts = pick(p["score"].loc[date], p["liquidity"].loc[date], p["age"].loc[date],
                             p["close"].loc[date], cfg)
        r = self.risk.risk_per_trade * self.breakers.r_multiplier()
        scale = r / self.risk.risk_per_trade
        blocked = self.breakers.entries_blocked(date)
        wanted: dict[str, tuple[float, float, float]] = {}
        for side, names in ((1.0, longs), (-1.0, shorts)):
            for s in names:
                if p["funding"].at[date, s] * side > cfg.funding_block_ann:
                    continue                                 # this side pays too much to hold
                price, vol = p["close"].at[date, s], p["vol"].at[date, s]
                dist = self._stop_distance(s, date, price)
                if not all(map(math.isfinite, (price, vol, dist))) or dist <= 0:
                    continue
                qty = equity * cfg.position_vol / (vol * price) * scale
                ceiling = equity * self.risk.max_position_risk / (dist * (1 + self.risk.slip(s))) * scale
                wanted[s] = (side * min(qty, ceiling), dist, price)
        capped = apply_portfolio_caps(equity, wanted, {}, self.risk) if wanted else {}

        out: dict[str, Target | None] = {}
        for s, pos in state.positions.items():
            cur = pos.qty
            target = capped.get(s, 0.0)
            if cur == 0 and target == 0:
                continue
            same_side = cur == 0 or target == 0 or math.copysign(1, cur) == math.copysign(1, target)
            if blocked and (abs(target) > abs(cur) or not same_side):
                target = cur if same_side else 0.0           # loss limit: may only reduce
            if cur != 0 and target != 0 and same_side and abs(target - cur) <= cfg.rebalance_threshold * abs(cur):
                continue                                     # kept: not worth the fees, stop stays where it is
            if target == 0:
                out[s] = Target(0.0)
                continue
            side = math.copysign(1, target)
            keep_stop = cur != 0 and same_side and pos.stop is not None
            stop = pos.stop if keep_stop else p["close"].at[date, s] - side * wanted[s][1]
            out[s] = Target(target, stop)
        self.log.append({"date": date, "longs": longs, "shorts": shorts, "r": r, "blocked": blocked})
        return out
