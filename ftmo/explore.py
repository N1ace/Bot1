"""Exploration outside the lab, at the owner's request (2026-11-01): one fixed set of stop rules on noise_area.

Nothing here can pass the lab. It uses visible data only, every variant is logged, and the luck check counts them all. A
winner is only an idea for a later lab test, under the lab's rules.

Stop rule: once a trade is ACTIVATE times its hard-stop distance in profit, the stop follows noise_trailing's level, less
OFFSET times half the band width, and never moves back. ACTIVATE 0 starts at entry.

    python -m ftmo.explore
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from ftmo.backtest import challenge_odds, load_days, run, sharpe
from ftmo.lab import TEST_VOL, deflated_sharpe, load_log
from ftmo.tactics import noise_area as base

ACTIVATE = (0.0, 0.5, 1.0, 2.0)
OFFSET = (0.0, 0.5, 1.0)
LOG = Path(__file__).resolve().parent / "explore_log.json"


def variant(activate: float, offset: float):
    def manage(day, minute, position, entry, price, lv):
        distance = lv.hard_stop[day] * entry
        if position * (price - entry) < activate * distance:
            return None
        width = (lv.upper[day, minute] - lv.lower[day, minute]) / 2
        if position > 0:
            return max(lv.upper[day, minute], lv.vwap[day, minute]) - offset * width
        return min(lv.lower[day, minute], lv.vwap[day, minute]) + offset * width

    return SimpleNamespace(levels=base.levels, decide=base.decide, CHECK_MINUTES=base.CHECK_MINUTES, manage=manage,
                           PAPER_VOL_TARGET=base.PAPER_VOL_TARGET, PAPER_MAX_EXPOSURE=base.PAPER_MAX_EXPOSURE)


def measure(days, other, tactic, trials: int) -> dict:
    result = run(days, tactic, base.SETTINGS, TEST_VOL)
    s = sharpe(result.returns)
    moved = [sharpe(run(days, tactic, dict(base.SETTINGS, **{key: base.SETTINGS[key] * factor}), TEST_VOL).returns)
             for key in base.TUNABLE for factor in (0.8, 1.2)]
    equity = np.cumprod(1 + result.returns)
    return {
        "sharpe": round(s, 3), "double_cost": round(sharpe(run(days, tactic, base.SETTINGS, TEST_VOL, cost_mult=2).returns), 3),
        "stability_gap": round(max(abs(s - x) for x in moved), 3), "worst_setting": round(min(moved), 3),
        "return_pct": round(float(equity[-1] - 1) * 100, 2),
        "max_drawdown_pct": round(float((equity / np.maximum.accumulate(equity) - 1).min()) * 100, 2),
        "trades": result.trades, "luck_check": round(deflated_sharpe(result.returns, trials), 3),
        "us500_sharpe": round(sharpe(run(other, tactic, base.SETTINGS, TEST_VOL).returns), 3),
        "odds_1pct": challenge_odds(run(days, tactic, base.SETTINGS, 0.01), attempts=500),
    }


def main() -> None:
    days, other = load_days("US100.cash"), load_days("US500.cash")
    trials = len({e["name"] for e in load_log()}) + len(ACTIVATE) * len(OFFSET)
    rows = []
    for activate in ACTIVATE:
        for offset in OFFSET:
            row = {"activate": activate, "offset": offset, **measure(days, other, variant(activate, offset), trials)}
            rows.append(row)
            print(row, flush=True)
    LOG.write_text(json.dumps({"when": datetime.now(timezone.utc).isoformat(timespec="seconds"), "trials_counted": trials,
                               "rows": rows}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
