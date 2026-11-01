"""Replay a tactic on FTMO's 1-minute bars with FTMO's costs and rules.

Pessimistic on purpose: orders fill at the next bar's open, a gap past a stop fills at the worse price, and spreads never
go below today's measured level, even where old history shows less. Lot rounding is ignored, as $10,000 sizes finely.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "ftmo"
FIRST_DAY = "2021-10-01"  # real 1-minute bars start around here
HOLDOUT_START = "2025-05-01"

SERVER_HOURS_AHEAD_OF_NY = 7  # checked against the US open across 2022-2026
SESSION_MINUTES = 390  # 09:30 to 16:00 New York
MIN_BARS_PER_DAY = 380

MEASURED_SPREAD = {"US100.cash": 1.45, "US500.cash": 0.60, "XAUUSD": 0.40}  # MT5, US hours, 2026
COMMISSION_PER_SIDE = {"XAUUSD": 0.000007}  # 0.0007% of the trade, from MT5. Indices have none
SLIPPAGE_SHARE = 0.25  # each fill also loses a quarter of the spread

DAY_STOP = -0.03  # the bot's own limits, inside FTMO's 5% and 10%
TOTAL_STOP = -0.07
MAX_SIZE_CHANGE = 1.25  # FTMO bans sharply varying sizes, so size moves slowly


@dataclass
class Days:
    """One row per session, one column per minute of the US session."""

    dates: pd.DatetimeIndex
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    ticks: np.ndarray
    spread: np.ndarray
    commission: float = 0.0


def load_days(symbol: str, include_holdout: bool = False) -> Days:
    bars = pd.read_pickle(DATA_DIR / f"{symbol}_M1.pkl")
    bars = bars[bars["time"] >= FIRST_DAY]
    if not include_holdout:
        bars = bars[bars["time"] < HOLDOUT_START]
    ny = bars["time"] - pd.Timedelta(hours=SERVER_HOURS_AHEAD_OF_NY)
    minute = (ny.dt.hour * 60 + ny.dt.minute) - (9 * 60 + 30)
    keep = (minute >= 0) & (minute < SESSION_MINUTES)
    bars = bars[keep].assign(day=ny[keep].dt.floor("D"), minute=minute[keep])

    tables = {}
    for column in ("open", "high", "low", "close", "tick_volume", "spread"):
        tables[column] = bars.pivot(index="day", columns="minute", values=column).reindex(columns=range(SESSION_MINUTES))
    complete = tables["close"].notna().sum(axis=1) >= MIN_BARS_PER_DAY
    for column, table in tables.items():
        table = table[complete].ffill(axis=1).bfill(axis=1)
        tables[column] = table.to_numpy(dtype=float)
    floor = MEASURED_SPREAD[symbol]
    return Days(
        dates=pd.DatetimeIndex(complete[complete].index),
        open=tables["open"], high=tables["high"], low=tables["low"], close=tables["close"],
        ticks=tables["tick_volume"], spread=np.maximum(tables["spread"], floor),
        commission=COMMISSION_PER_SIDE.get(symbol, 0.0),
    )


def position_sizes(days: Days, target_daily_vol: float, max_exposure: float) -> np.ndarray:
    """Exposure per day from past volatility only, with changes limited from one day to the next."""
    daily = pd.Series(days.close[:, -1]).pct_change()
    vol = daily.rolling(14).std().shift(1).to_numpy()
    sizes = np.zeros(len(vol))
    previous = 0.0
    for i, v in enumerate(vol):
        if np.isnan(v) or v <= 0:
            continue
        wanted = min(max_exposure, target_daily_vol / v)
        previous = wanted if previous == 0 else float(np.clip(wanted, previous / MAX_SIZE_CHANGE, previous * MAX_SIZE_CHANGE))
        sizes[i] = previous
    return sizes


@dataclass
class Result:
    dates: pd.DatetimeIndex
    returns: np.ndarray  # each day's return on that morning's balance
    worst: np.ndarray  # lowest point during each day, against that morning's balance
    trades: int


def run(days: Days, tactic, settings: dict, target_daily_vol: float, cost_mult: float = 1.0,
        random_seed: int | None = None) -> Result:
    """A tactic with LAYERS splits its position equally across versions of itself.

    Daily lows are averaged too. The true low of the mix can't be below that, so this errs on the cautious side.
    """
    layers = getattr(tactic, "LAYERS", None)
    if layers is None:
        return run_one(days, tactic, settings, target_daily_vol, cost_mult, random_seed)
    parts = []
    for i, layer in enumerate(layers):
        seed = None if random_seed is None else random_seed * len(layers) + i
        parts.append(run_one(days, tactic, tactic.layer_settings(settings, layer), target_daily_vol, cost_mult, seed))
    return Result(days.dates, np.mean([r.returns for r in parts], axis=0), np.mean([r.worst for r in parts], axis=0),
                  sum(r.trades for r in parts))


def run_one(days: Days, tactic, settings: dict, target_daily_vol: float, cost_mult: float = 1.0,
            random_seed: int | None = None) -> Result:
    """Random_seed swaps the tactic's direction for a coin flip, keeping everything else."""
    rng = None if random_seed is None else np.random.default_rng(random_seed)
    levels = tactic.levels(days, settings)
    sizes = position_sizes(days, target_daily_vol, target_daily_vol / tactic.PAPER_VOL_TARGET * tactic.PAPER_MAX_EXPOSURE)
    returns, worst = np.zeros(len(sizes)), np.zeros(len(sizes))
    trades = 0
    checks = list(tactic.CHECK_MINUTES)

    for d in np.flatnonzero(sizes > 0):
        if not levels.ready[d]:
            continue
        size = sizes[d]
        o, h, low, c, spread = days.open[d], days.high[d], days.low[d], days.close[d], days.spread[d]
        pnl, lowest, position, entry, stop = 0.0, 0.0, 0, 0.0, 0.0
        halted = False

        def fee(minute: int) -> float:
            return size * cost_mult * (spread[minute] * (0.5 + SLIPPAGE_SHARE) / o[0] + days.commission)

        for start, end in zip(checks, checks[1:] + [SESSION_MINUTES - 1]):
            if not halted:
                target = tactic.decide(d, start - 1, c[start - 1], position, levels)
                if target != 0 and position == 0 and rng is not None:
                    target = int(rng.choice([-1, 1]))
                elif rng is not None and target == -position and position != 0:
                    target = int(rng.choice([-1, 1]))
                if target != position:
                    if position != 0:
                        pnl += position * size * (o[start] - entry) / o[0] - fee(start)
                    position = target
                    if position != 0:
                        entry = o[start]
                        stop = entry - position * levels.hard_stop[d] * entry
                        pnl -= fee(start)
                        trades += 1
            if position == 0:
                continue

            # Stop level: the tactic's own, or the price where today's loss reaches the bot's limit
            room = (pnl - DAY_STOP) / size * o[0]
            day_limit = entry - position * room
            trigger = max(stop, day_limit) if position > 0 else min(stop, day_limit)
            hit = low[start:end] <= trigger if position > 0 else h[start:end] >= trigger
            if hit.any():
                m = start + int(np.argmax(hit))
                fill = min(o[m], trigger) if position > 0 else max(o[m], trigger)
                pnl += position * size * (fill - entry) / o[0] - fee(m)
                lowest = min(lowest, pnl)
                position = 0
                halted = bool(pnl <= DAY_STOP + 1e-9) or halted
                continue
            adverse = low[start:end].min() if position > 0 else h[start:end].max()
            lowest = min(lowest, pnl + position * size * (adverse - entry) / o[0])

        if position != 0:
            pnl += position * size * (o[-1] - entry) / o[0] - fee(SESSION_MINUTES - 1)
        returns[d] = pnl
        worst[d] = min(lowest, pnl)
    return Result(days.dates, returns, worst, trades)


def sharpe(returns: np.ndarray) -> float:
    active = returns[np.flatnonzero(np.cumsum(np.abs(returns)) > 0)[0]:] if np.any(returns) else returns
    std = active.std(ddof=1)
    return float(active.mean() / std * np.sqrt(252)) if std > 0 else 0.0


def challenge_once(returns: np.ndarray, worst: np.ndarray) -> tuple[str, int]:
    """Play one 2-Step attempt. Returns (passed, failed or unfinished, trading days used)."""
    days_used = 0
    i = 0
    for target in (0.10, 0.05):
        balance, active = 1.0, 0
        while True:
            if i >= len(returns):
                return "unfinished", days_used
            low_point = balance * (1 + worst[i])
            balance *= 1 + returns[i]
            active += returns[i] != 0
            days_used += 1
            i += 1
            if low_point <= 1 + TOTAL_STOP or worst[i - 1] <= -0.05:
                return "failed", days_used
            if balance >= 1 + target and active >= 4:
                break
    return "passed", days_used


def challenge_odds(result: Result, attempts: int = 2000, years: float = 3.0, block_days: int = 10,
                   seed: int = 1) -> dict:
    """Shuffle blocks of days into many possible attempts, each with up to `years` to finish."""
    rng = np.random.default_rng(seed)
    n = len(result.returns)
    length = int(years * 252)
    outcomes, passed_days = {"passed": 0, "failed": 0, "unfinished": 0}, []
    for _ in range(attempts):
        starts = rng.integers(0, n - block_days, length // block_days + 1)
        picks = np.concatenate([np.arange(s, s + block_days) for s in starts])[:length]
        outcome, used = challenge_once(result.returns[picks], result.worst[picks])
        outcomes[outcome] += 1
        if outcome == "passed":
            passed_days.append(used)
    return {
        "pass": outcomes["passed"] / attempts, "fail": outcomes["failed"] / attempts,
        "unfinished": outcomes["unfinished"] / attempts,
        "median_days_to_pass": float(np.median(passed_days)) if passed_days else None,
    }
