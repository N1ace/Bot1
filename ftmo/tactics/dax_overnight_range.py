"""DAX overnight-range breakout.

HYPOTHESIS: the Xetra open brings the day's first real European order flow, and it tends to resolve the range built while
Europe slept. A break of that range at the open tends to run far enough to pay twice what it risks.

Rules were fixed from the published study (a VPS seller's, so an upper bound) before any test here:
- The range is 03:00 to 09:00 Frankfurt. From 09:00, the first touch of its high goes long, of its low goes short.
- The stop is the other side of the range. The target is 2 times the risk. Flat by 13:00. One trade a day.
- If a bar touches both sides, the day is skipped. If a stop and the target share a bar, the stop counts.
- None of the report's optional skips (very wide ranges, ECB days) are used.
"""

from dataclasses import dataclass

import numpy as np

from ftmo.backtest import DAY_STOP, SLIPPAGE_SHARE, Result, position_sizes

SYMBOL = "GER40.cash"
INFO_SYMBOL = None
SETTINGS = {"range_hours": 6.0, "target_r": 2.0}
TUNABLE = ("range_hours", "target_r")

OPEN_MINUTE = 480  # 09:00 Frankfurt, on a grid that starts at 01:00
CHECK_MINUTES = (OPEN_MINUTE,)
PAPER_VOL_TARGET = 0.02  # same sizing frame as the other tactics
PAPER_MAX_EXPOSURE = 4.0


@dataclass
class Levels:
    high: np.ndarray
    low: np.ndarray
    ready: np.ndarray


def levels(days, settings: dict) -> Levels:
    start = OPEN_MINUTE - int(round(settings["range_hours"] * 60))
    high = days.high[:, start:OPEN_MINUTE].max(axis=1)
    low = days.low[:, start:OPEN_MINUTE].min(axis=1)
    return Levels(high, low, high > low)


def decide(day: int, minute: int, price: float, position: int, lv: Levels) -> int:
    return position  # entries are stop orders, handled in simulate


def simulate(days, settings: dict, target_daily_vol: float, cost_mult: float = 1.0,
             random_seed: int | None = None) -> Result:
    """Stop-order entries don't fit the half-hour loop, so this tactic replays its own bars."""
    rng = None if random_seed is None else np.random.default_rng(random_seed)
    lv = levels(days, settings)
    sizes = position_sizes(days, target_daily_vol, target_daily_vol / PAPER_VOL_TARGET * PAPER_MAX_EXPOSURE)
    returns, worst = np.zeros(len(sizes)), np.zeros(len(sizes))
    trades = 0

    for d in np.flatnonzero((sizes > 0) & lv.ready):
        size, top, bottom = sizes[d], lv.high[d], lv.low[d]
        o, h, low, c, spread = days.open[d], days.high[d], days.low[d], days.close[d], days.spread[d]
        up, down = h[OPEN_MINUTE:] >= top, low[OPEN_MINUTE:] <= bottom
        touched = up | down
        if not touched.any():
            continue
        m = OPEN_MINUTE + int(np.argmax(touched))
        if up[m - OPEN_MINUTE] and down[m - OPEN_MINUTE]:
            continue

        broke_up = bool(up[m - OPEN_MINUTE])
        entry = max(o[m], top) if broke_up else min(o[m], bottom)
        risk = entry - bottom if broke_up else top - entry
        direction = 1 if broke_up else -1
        if rng is not None:
            direction = int(rng.choice([-1, 1]))
        if risk <= 0:
            continue

        def fee(minute: int) -> float:
            return size * cost_mult * (spread[minute] * (0.5 + SLIPPAGE_SHARE) / entry + days.commission)

        trades += 1
        pnl = -fee(m)
        stop = entry - direction * risk
        target = entry + direction * settings["target_r"] * risk
        day_limit = entry - direction * (pnl - DAY_STOP) / size * entry
        trigger = max(stop, day_limit) if direction > 0 else min(stop, day_limit)

        later_low, later_high = low[m + 1:], h[m + 1:]
        stopped = later_low <= trigger if direction > 0 else later_high >= trigger
        reached = later_high >= target if direction > 0 else later_low <= target
        stop_at = int(np.argmax(stopped)) if stopped.any() else len(stopped)
        target_at = int(np.argmax(reached)) if reached.any() else len(reached)

        if stop_at < len(stopped) and stop_at <= target_at:
            k = m + 1 + stop_at
            exit_price = min(o[k], trigger) if direction > 0 else max(o[k], trigger)
        elif target_at < len(reached):
            k = m + 1 + target_at
            exit_price = target
        else:
            k = len(c) - 1
            exit_price = c[k]

        adverse = low[m:k + 1].min() if direction > 0 else h[m:k + 1].max()
        pnl_low = pnl + direction * size * (adverse - entry) / entry
        pnl += direction * size * (exit_price - entry) / entry - fee(k)
        returns[d] = pnl
        worst[d] = min(pnl_low, pnl)

    return Result(days.dates, returns, worst, trades)
