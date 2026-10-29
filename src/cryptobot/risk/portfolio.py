"""Portfolio risk model, layers 1-4 of strategy-risk-spec.md section 3.

Turns "how bullish is each symbol" (the strategy signal) into "how many coins
to hold", inside hard limits. Pure functions + one small stateful class
(CircuitBreakers) so everything can be unit-tested and reused live.

Layer 1  per-trade size:  the smaller of
           risk size  = equity x r / (stop distance x (1 + slip buffer))
           vol size   = equity x vol_target_asset / (annual volatility x price)
         times the signal strength (3/7 long -> 3/7 of the size).
Layer 2  leverage: total |notional| <= max_eff_leverage x equity, and the stop must
         sit well before liquidation (stop distance <= stop_to_liq_max x liq distance).
Layer 3  portfolio: max positions, total open risk ("heat") <= max_heat, and the
         correlated BTC/ETH/SOL cluster's combined risk <= cluster_heat.
Layer 4  circuit breakers: daily -2% and weekly -4% loss block new risk; drawdown
         -10% / -15% cut r to 0.5x / 0.25x, -20% flattens and stops; 6 losing trades
         in a row halve r for the next 10 trades.

"Risk" of a position = how much it loses if the stop is hit = qty x stop distance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd


@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade: float = 0.005            # r: 0.5% of equity lost if a full-size position hits its stop
    vol_target_asset: float = 0.15
    k_atr: float = 3.0                       # stop distance = k_atr x ATR(20)
    slip_buffer: dict[str, float] = field(default_factory=lambda: {"BTCUSDT": 0.25, "ETHUSDT": 0.25,
                                                                   "default": 0.5})
    isolated_leverage: int = 2
    maint_margin_rate: float = 0.004
    stop_to_liq_max: float = 0.33
    max_eff_leverage: float = 1.5
    max_positions: int = 4
    max_heat: float = 0.02
    cluster: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    cluster_heat: float = 0.0125
    corr_floor: float = 0.7
    max_net_notional: float = 1.25           # same-direction net notional, x equity
    daily_loss: float = 0.02
    weekly_loss: float = 0.04
    dd_steps: tuple[tuple[float, float], ...] = ((0.10, 0.5), (0.15, 0.25))
    dd_stop: float = 0.20
    losing_streak_count: int = 6
    losing_streak_mult: float = 0.5
    losing_streak_trades: int = 10

    def slip(self, symbol: str) -> float:
        return self.slip_buffer.get(symbol, self.slip_buffer.get("default", 0.5))

    @property
    def liq_distance(self) -> float:
        """Approximate isolated liquidation distance as a fraction of price: 1/L - MMR."""
        return 1 / self.isolated_leverage - self.maint_margin_rate


# Spec "hard limit" column (strategy-risk-spec.md section 3): config may be stricter, never looser.
HARD_LIMITS = {
    "isolated_leverage": 3,       # = config.MAX_LEVERAGE_HARD_CAP
    "max_eff_leverage": 2.5,
    "max_positions": 6,
    "max_heat": 0.03,
    "cluster_heat": 0.015,
    "max_net_notional": 2.0,
}


def validate_hard_limits(cfg: RiskConfig) -> None:
    """Refuse a config that is looser than the spec's hard limits (used for anything that may trade)."""
    too_high = {k: (getattr(cfg, k), v) for k, v in HARD_LIMITS.items() if getattr(cfg, k) > v}
    if too_high:
        raise ValueError("Risk config above hard limits: " +
                         ", ".join(f"{k}={got} (max {limit})" for k, (got, limit) in too_high.items()))
    if not 0 < cfg.risk_per_trade <= 0.01:
        raise ValueError(f"risk_per_trade {cfg.risk_per_trade} outside (0, 1%]")


@dataclass(frozen=True)
class SymbolInput:
    price: float
    atr: float                # ATR(20) in price units
    vol_ann: float            # annualised realised volatility, e.g. 0.6 = 60%
    signal: float             # ensemble signal: 1 = full long, 0 = flat, negative = short


# ---- Layer 1 + liquidation part of layer 2 -----------------------------------------

def size_position(equity: float, symbol: str, inp: SymbolInput, cfg: RiskConfig, r: float) -> tuple[float, float]:
    """(signed qty, stop distance in price units). qty 0 when the trade is not allowed."""
    if inp.signal == 0 or equity <= 0 or not all(map(math.isfinite, (inp.price, inp.atr, inp.vol_ann))):
        return 0.0, math.nan
    stop_dist = cfg.k_atr * inp.atr
    if stop_dist <= 0 or inp.vol_ann <= 0:
        return 0.0, math.nan
    # Layer 2: the stop must trigger long before liquidation would.
    if stop_dist / inp.price > cfg.stop_to_liq_max * cfg.liq_distance:
        return 0.0, stop_dist
    qty_risk = equity * r / (stop_dist * (1 + cfg.slip(symbol)))
    qty_vol = equity * cfg.vol_target_asset / (inp.vol_ann * inp.price)
    return math.copysign(min(qty_risk, qty_vol) * abs(inp.signal), inp.signal), stop_dist


# ---- Layers 2-3: portfolio caps ---------------------------------------------------------

def cluster_risk(risks: dict[str, float], corr: dict[tuple[str, str], float], floor: float) -> float:
    """sqrt(sum_i sum_j rho_ij r_i r_j), rho floored (crashes hit all big coins together)."""
    total = 0.0
    for a, ra in risks.items():
        for b, rb in risks.items():
            rho = 1.0 if a == b else max(corr.get((a, b), corr.get((b, a), 1.0)), floor)
            total += rho * ra * rb
    return math.sqrt(max(total, 0.0))


def apply_portfolio_caps(equity: float, wanted: dict[str, tuple[float, float, float]],
                         corr: dict[tuple[str, str], float], cfg: RiskConfig) -> dict[str, float]:
    """wanted: symbol -> (signed qty, stop distance, price). Returns capped qty per symbol.

    Caps scale positions down proportionally (keeps the mix, shrinks the total).
    """
    qty = {s: q for s, (q, _, _) in wanted.items() if q != 0}
    # Max concurrent positions: keep the ones with the largest risk.
    if len(qty) > cfg.max_positions:
        keep = sorted(qty, key=lambda s: abs(qty[s]) * wanted[s][1], reverse=True)[:cfg.max_positions]
        qty = {s: qty[s] for s in keep}

    def risk(s):            # fraction of equity lost if the stop is hit
        return abs(qty[s]) * wanted[s][1] / equity

    def scale_all(factor, names=None):
        for s in (names if names is not None else list(qty)):
            qty[s] *= factor

    heat = sum(risk(s) for s in qty)
    if heat > cfg.max_heat:
        scale_all(cfg.max_heat / heat)
    in_cluster = [s for s in qty if s in cfg.cluster]
    c_risk = cluster_risk({s: risk(s) for s in in_cluster}, corr, cfg.corr_floor)
    if c_risk > cfg.cluster_heat:
        scale_all(cfg.cluster_heat / c_risk, in_cluster)
    gross = sum(abs(q) * wanted[s][2] for s, q in qty.items())
    if gross > cfg.max_eff_leverage * equity:
        scale_all(cfg.max_eff_leverage * equity / gross)
    for sign in (1, -1):
        side = [s for s, q in qty.items() if math.copysign(1, q) == sign]
        net = sum(abs(qty[s]) * wanted[s][2] for s in side)
        if net > cfg.max_net_notional * equity:
            scale_all(cfg.max_net_notional * equity / net, side)
    return {s: qty.get(s, 0.0) for s in wanted}


# ---- Layer 4: circuit breakers ----------------------------------------------------------

class CircuitBreakers:
    """Remembers equity history and trade results; answers 'how much risk today?'."""

    def __init__(self, cfg: RiskConfig):
        self.cfg = cfg
        self.high_water = None
        self.dd_mult = 1.0             # drawdown step currently in force
        self.dd_step_low = None        # equity low while a step is active (for recovery)
        self.stopped = False           # -20%: flatten and stop for manual review
        self.blocked_until: pd.Timestamp | None = None
        self.prev_close_equity = None
        self.week_start_equity = None
        self.week = None
        self.losses_in_row = 0
        self.reduced_trades_left = 0
        self.events: list[tuple[pd.Timestamp, str]] = []

    def update(self, date: pd.Timestamp, equity: float) -> None:
        """Call once per day at the close, before sizing tomorrow's targets."""
        cfg = self.cfg
        if self.high_water is None:
            self.high_water = self.prev_close_equity = self.week_start_equity = equity
            self.week = date.isocalendar()[:2]
        # weekly bookkeeping (ISO week, UTC)
        if date.isocalendar()[:2] != self.week:
            self.week, self.week_start_equity = date.isocalendar()[:2], self.prev_close_equity
        # daily / weekly loss -> block new risk
        if equity <= self.prev_close_equity * (1 - cfg.daily_loss):
            self._block(date + pd.Timedelta(days=1), date, "daily loss limit")
        if equity <= self.week_start_equity * (1 - cfg.weekly_loss):
            days_to_monday = 7 - date.weekday()
            self._block(date + pd.Timedelta(days=days_to_monday), date, "weekly loss limit")
        # drawdown steps
        self.high_water = max(self.high_water, equity)
        dd = 1 - equity / self.high_water
        if dd >= cfg.dd_stop and not self.stopped:
            self.stopped = True
            self.events.append((date, f"drawdown {dd:.1%}: FLATTEN AND STOP"))
        new_mult = 1.0
        for level, mult in cfg.dd_steps:
            if dd >= level:
                new_mult = min(new_mult, mult)
        if new_mult < self.dd_mult:
            self.dd_mult, self.dd_step_low = new_mult, equity
            self.events.append((date, f"drawdown {dd:.1%}: risk x{new_mult}"))
        elif self.dd_mult < 1.0:
            # step back up one level only after recovering half of the drawdown
            self.dd_step_low = min(self.dd_step_low, equity)
            if equity >= self.dd_step_low + (self.high_water - self.dd_step_low) / 2:
                levels = [1.0] + [m for _, m in cfg.dd_steps]
                self.dd_mult = levels[max(levels.index(self.dd_mult) - 1, 0)]
                self.dd_step_low = equity
                self.events.append((date, f"recovered: risk x{self.dd_mult}"))
        self.prev_close_equity = equity

    def _block(self, until: pd.Timestamp, date: pd.Timestamp, why: str) -> None:
        if self.blocked_until is None or until > self.blocked_until:
            self.blocked_until = until
            self.events.append((date, f"{why}: no new risk until {until:%Y-%m-%d}"))

    def record_trade(self, pnl: float) -> None:
        """Call when a position is fully closed (pnl after fees)."""
        if self.reduced_trades_left > 0:
            self.reduced_trades_left -= 1
        self.losses_in_row = self.losses_in_row + 1 if pnl < 0 else 0
        if self.losses_in_row >= self.cfg.losing_streak_count:
            self.reduced_trades_left = self.cfg.losing_streak_trades
            self.losses_in_row = 0

    # ---- persistence: a restart must never reset a halt (CLAUDE.md rule 4) -------------

    _FIELDS = ("high_water", "dd_mult", "dd_step_low", "stopped", "blocked_until", "prev_close_equity",
               "week_start_equity", "week", "losses_in_row", "reduced_trades_left")

    def to_state(self) -> dict:
        state = {k: getattr(self, k) for k in self._FIELDS}
        state["blocked_until"] = self.blocked_until.isoformat() if self.blocked_until is not None else None
        state["week"] = list(self.week) if self.week is not None else None
        return state

    @classmethod
    def from_state(cls, cfg: RiskConfig, state: dict | None) -> CircuitBreakers:
        cb = cls(cfg)
        for k, v in (state or {}).items():
            if k in cls._FIELDS:
                setattr(cb, k, v)
        if cb.blocked_until is not None:
            cb.blocked_until = pd.Timestamp(cb.blocked_until)
        if cb.week is not None:
            cb.week = tuple(cb.week)
        return cb

    def entries_blocked(self, today: pd.Timestamp) -> bool:
        """True if orders decided at today's close may not add risk.

        Daily bars: a daily-loss hit today sets blocked_until = tomorrow, so the order
        filling at tomorrow's open can't add risk; tomorrow's own decision is free again.
        """
        return self.stopped or (self.blocked_until is not None and today < self.blocked_until)

    def r_multiplier(self) -> float:
        streak = self.cfg.losing_streak_mult if self.reduced_trades_left > 0 else 1.0
        return 0.0 if self.stopped else self.dd_mult * streak
