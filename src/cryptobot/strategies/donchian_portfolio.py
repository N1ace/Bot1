"""Donchian ensemble + portfolio risk model, as one portfolio strategy.

Each day at the close it:
  1. updates the circuit breakers (daily/weekly loss, drawdown steps, losing streak)
  2. reads each symbol's ensemble signal (strategies/donchian.py)
  3. sizes each position (risk layer 1), then applies the portfolio caps (layers 2-3)
  4. applies blocks: no new risk while a loss limit is active; flatten if stopped
  5. skips small changes (rebalance threshold) to save fees - but never skips a
     reduction that a portfolio cap asked for
  6. sets the catastrophe stop and TRAILS it: every day stop = max(old stop,
     close - k_atr x ATR) for a long (min(...) + for a short). It never loosens.

Deviation from the spec (on purpose, flagged by the risk review): the spec puts the
catastrophe stop 3xATR "from entry". A fixed entry stop lets the real risk grow far
beyond r as the price runs away from entry (a crash would give back all of it, and
the heat cap would not see it). Trailing keeps "risk if the stop hits" = what the
heat/cluster caps measure. Heat is computed against the stop that will actually be
on the exchange.

Indicator series are computed once for the whole history. Every one of them only
looks backwards (rolling windows), so day t's value is the same whether or not
later data exists - tests/test_strategy_risk.py checks this.

Re-entry after a stop-out: the symbol stays flat until the signal is stronger than
it was when the stop hit, or until it has dropped to 0 (a fresh start). Without
this the bot would buy straight back into a crash because slow sub-signals are
still "long".
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
from cryptobot.risk.overlay import OverlayConfig, overlay, regime_frame, validate_overlay
from cryptobot.risk.portfolio import (CircuitBreakers, RiskConfig, SymbolInput, apply_portfolio_caps, size_position,
                                      validate_hard_limits)
from cryptobot.strategies.donchian import DEFAULT_LOOKBACKS, ensemble_signal


@dataclass(frozen=True)
class StrategyConfig:
    lookbacks: tuple[int, ...] = DEFAULT_LOOKBACKS
    allow_shorts: bool = False
    short_weight: float = 0.5
    rebalance_threshold: float = 0.20
    atr_n: int = 20
    vol_window: int = 60


def load_overlay_config(raw: dict) -> OverlayConfig:
    """Regime overlay + funding layer settings from the strategy.yaml dict."""
    g, f = raw.get("regime", {}), raw.get("funding", {})
    cfg = OverlayConfig(
        enabled=bool(g.get("enabled", True)), btc_sma=int(g.get("btc_sma", 200)),
        sma_slope_days=int(g.get("sma_slope_days", 20)), er_n=int(g.get("er_n", 20)),
        er_trend=float(g.get("er_trend", 0.30)), er_chop=float(g.get("er_chop", 0.15)),
        adx_n=int(g.get("adx_n", 14)), adx_trend=float(g.get("adx_trend", 25)), adx_chop=float(g.get("adx_chop", 18)),
        chop_mult=float(g.get("chop_mult", 0.5)), vol_window=int(g.get("vol_window", 30)),
        vol_pct_window_days=int(g.get("vol_pct_window_days", 365)), vol_high_pct=float(g.get("vol_high_pct", 0.90)),
        vol_high_mult=float(g.get("vol_high_mult", 0.5)), funding_z_block=float(g.get("funding_z_block", 2.0)),
        funding_z_days=int(g.get("funding_z_days", 90)),
        funding_halve_ann=float(f.get("halve_above_ann", 0.30)), funding_exit_ann=float(f.get("exit_above_ann", 0.60)),
    )
    validate_overlay(cfg)
    return cfg


def load_strategy_config(path: str | Path = "config/strategy.yaml") -> tuple[StrategyConfig, RiskConfig, dict]:
    """Returns (strategy settings, risk settings, raw dict for costs/symbols/regime/funding).

    `risk_profile:` in the file picks which hard limits apply (conservative by default;
    config/core.yaml uses the owner's aggressive trading budget)."""
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    s, r = raw["strategy"], raw["risk"]
    strat = StrategyConfig(
        lookbacks=tuple(s["lookbacks"]), allow_shorts=bool(s["allow_shorts"]),
        short_weight=float(s["short_weight"]), rebalance_threshold=float(s["rebalance_threshold"]),
        atr_n=int(s["atr_n"]), vol_window=int(s.get("vol_window", 60)),
    )
    risk = RiskConfig(
        risk_per_trade=float(r["risk_per_trade"]), vol_target_asset=float(r["vol_target_asset"]),
        k_atr=float(s["k_atr"]), slip_buffer=dict(r["slip_buffer"]),
        isolated_leverage=int(r["isolated_leverage"]), stop_to_liq_max=float(r["stop_to_liq_max"]),
        max_eff_leverage=float(r["max_eff_leverage"]), max_positions=int(r["max_positions"]),
        max_heat=float(r["max_heat"]), cluster=tuple(r["cluster"]), cluster_heat=float(r["cluster_heat"]),
        corr_floor=float(r["corr_floor"]), max_net_notional=float(r["max_net_notional"]),
        daily_loss=float(r["daily_loss"]), weekly_loss=float(r["weekly_loss"]),
        dd_steps=tuple(sorted((float(k), float(v)) for k, v in r["dd_steps"].items())),
        dd_stop=float(r["dd_stop"]), losing_streak_count=int(r["losing_streak"]["count"]),
        losing_streak_mult=float(r["losing_streak"]["mult"]),
        losing_streak_trades=int(r["losing_streak"]["trades"]),
        sizing=str(r.get("sizing", "risk")), max_position_risk=float(r.get("max_position_risk", 0.01)),
    )
    validate_hard_limits(risk, raw.get("risk_profile", "conservative"))
    return strat, risk, raw


def precompute(bars: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    """signal, ATR and annualised volatility per day, using only past and present data."""
    out = pd.DataFrame(index=bars.index)
    out["signal"] = ensemble_signal(bars["close"], cfg.lookbacks, cfg.allow_shorts, cfg.short_weight)
    out["atr"] = indicators.atr(bars, cfg.atr_n)
    log_ret = np.log(bars["close"]).diff()
    out["vol_ann"] = log_ret.rolling(cfg.vol_window).std() * math.sqrt(365)
    out["log_ret"] = log_ret
    return out


class DonchianPortfolio:
    def __init__(self, bars: dict[str, pd.DataFrame], strat: StrategyConfig, risk: RiskConfig,
                 breaker_state: dict | None = None, funding: dict[str, pd.DataFrame] | None = None,
                 overlay_cfg: OverlayConfig | None = None, btc_bars: pd.DataFrame | None = None):
        """
        breaker_state: saved CircuitBreakers.to_state() (live: loaded from SQLite)
        funding:       funding history per symbol (for the funding layer)
        overlay_cfg:   regime overlay + funding layer settings; None = overlay off
        btc_bars:      BTC daily bars for the market state (defaults to bars['BTCUSDT'])
        """
        self.strat, self.risk = strat, risk
        self.data = {s: precompute(df, strat) for s, df in bars.items()}
        self.overlay_cfg = overlay_cfg or OverlayConfig(enabled=False)
        validate_overlay(self.overlay_cfg)
        btc = btc_bars if btc_bars is not None else bars.get("BTCUSDT")
        self.regime: dict[str, pd.DataFrame] = {}
        self.overlay_warnings: list[str] = []
        if self.overlay_cfg.enabled:
            if btc is None or btc.empty:
                raise ValueError("Regime overlay is on but there are no BTCUSDT bars for the market state")
            for s, df in bars.items():
                self.regime[s] = regime_frame(df, btc, (funding or {}).get(s), self.overlay_cfg)
                missing = int(self.regime[s]["funding_missing"].sum())
                if missing:
                    self.overlay_warnings.append(f"{s}: no funding data on {missing} of {len(df)} days "
                                                 "(funding layer inactive on those days)")
        rets = pd.DataFrame({s: d["log_ret"] for s, d in self.data.items()})
        self.corr = {(a, b): rets[a].rolling(strat.vol_window).corr(rets[b])
                     for i, a in enumerate(rets) for b in list(rets)[i + 1:]}
        self.breakers = CircuitBreakers.from_state(risk, breaker_state)
        self.stop_block: dict[str, float] = {}       # symbol -> signed signal when its stop hit
        self.log: list[dict] = []                     # daily decisions, for inspection

    def _trail_stop(self, old: float | None, side: float, price: float, atr: float) -> float:
        """Catastrophe stop k x ATR from today's close, never looser than the old one."""
        fresh = price - self.risk.k_atr * atr if side > 0 else price + self.risk.k_atr * atr
        if old is None or not math.isfinite(fresh):
            return fresh if math.isfinite(fresh) else old
        return max(old, fresh) if side > 0 else min(old, fresh)

    def _blocked_after_stop(self, symbol: str, signal: float) -> bool:
        """After a stop-out stay flat until: signal 0 / other side (fresh start) or a
        stronger signal on the same side than when the stop hit."""
        if symbol not in self.stop_block:
            return False
        at_stop = self.stop_block[symbol]
        if signal == 0 or math.copysign(1, signal) != math.copysign(1, at_stop) or abs(signal) > abs(at_stop):
            del self.stop_block[symbol]
            return False
        return True

    def targets(self, histories: dict[str, pd.DataFrame], state: PortfolioState) -> dict[str, Target | None]:
        date, equity = state.date, state.equity
        for _, pnl in state.closed_trades:
            self.breakers.record_trade(pnl)
        self.breakers.update(date, equity)

        if self.breakers.stopped:     # -20% drawdown: flatten everything and stay flat
            return {s: Target(0.0) for s, p in state.positions.items() if p.qty != 0}

        r = self.risk.risk_per_trade * self.breakers.r_multiplier()
        blocked = self.breakers.entries_blocked(date)
        wanted, rows = {}, {}
        no_add: set[str] = set()                         # crowded: may hold or reduce, not add
        reasons: dict[str, tuple[str, ...]] = {}
        for s in histories:
            row = self.data[s].loc[date]
            signal = row["signal"]
            if s in state.stopped_out:
                self.stop_block[s] = signal
            if self._blocked_after_stop(s, signal):
                signal = 0.0                             # stay out after a stop-out
            price, atr = histories[s]["close"].iloc[-1], row["atr"]
            pos = state.positions[s]
            qty, _ = size_position(equity, s, SymbolInput(price, atr, row["vol_ann"], signal), self.risk, r)
            # Regime overlay + funding layer: shrink (never grow) the size.
            decision = (overlay(self.regime[s].loc[date], math.copysign(1, qty), signal, self.overlay_cfg)
                        if qty != 0 and s in self.regime else None)
            if decision is not None:
                qty *= decision.multiplier
                if decision.block_increase:
                    no_add.add(s)
                reasons[s] = decision.reasons
            # Risk is measured against the stop that will really be on the exchange.
            stop, dist = None, math.nan
            if qty != 0:
                side = math.copysign(1, qty)
                old = pos.stop if pos.qty != 0 and math.copysign(1, pos.qty) == side else None
                stop = self._trail_stop(old, side, price, atr)
                dist = abs(price - stop) if stop is not None else math.nan
            wanted[s] = (qty, dist, price)
            rows[s] = (price, atr)
        corr_today = {k: (v.loc[date] if not math.isnan(v.loc[date]) else 1.0) for k, v in self.corr.items()}
        capped = apply_portfolio_caps(equity, wanted, corr_today, self.risk)

        out: dict[str, Target | None] = {}
        for s, target in capped.items():
            pos, (price, atr) = state.positions[s], rows[s]
            cur = pos.qty
            cap_cut = abs(target) < abs(wanted[s][0]) * (1 - 1e-9)    # a portfolio cap shrank it
            same_side = cur == 0 or target == 0 or math.copysign(1, cur) == math.copysign(1, target)
            if (blocked or s in no_add) and (abs(target) > abs(cur) or not same_side):
                target = cur if same_side else 0.0       # loss limit / crowding: may only reduce
            small_change = (cur != 0 and target != 0 and same_side
                            and abs(target - cur) <= self.strat.rebalance_threshold * abs(cur))
            if small_change and not (cap_cut and abs(target) < abs(cur)):
                target = cur                             # not worth the fees - but keep trailing the stop
            if target == 0 and cur == 0:
                out[s] = None
                continue
            stop = None
            if target != 0:
                side = math.copysign(1, target)
                old = pos.stop if cur != 0 and math.copysign(1, cur) == side else None
                stop = self._trail_stop(old, side, price, atr)
                if stop is None:
                    target = 0.0                         # never hold a position without a stop
            if target == cur and stop == pos.stop:
                out[s] = None                            # nothing changes
                continue
            out[s] = Target(qty=target, stop=stop)
            self.log.append({"date": date, "symbol": s, "signal": self.data[s].loc[date, "signal"],
                             "target": target, "current": cur, "stop": stop, "r": r, "blocked": blocked,
                             "overlay": reasons.get(s, ())})
        return out
