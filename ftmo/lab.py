"""Honest testing for FTMO tactics: limited budget, one test per version, hidden data.

    python -m ftmo.lab board
    python -m ftmo.lab test noise_area
    python -m ftmo.lab final noise_area

Gates are fixed here, before any result. Every attempt counts, including failures.
"""

import argparse
import hashlib
import importlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist

import numpy as np

from ftmo.backtest import challenge_odds, load_days, run, sharpe

norm = NormalDist()
LOG = Path(__file__).resolve().parent / "lab_log.json"
MAX_TACTICS = 12
SYMBOL = "US100.cash"  # the gates apply here; US500 is shown for information only
TEST_VOL = 0.005  # a quarter of the paper's 2% target
CHALLENGE_VOLS = (0.005, 0.01, 0.02)
RANDOM_RUNS = 200

GATES = {"trades": 300, "sharpe": 0.8, "double_cost_sharpe": 0.4, "stability_gap": 0.25, "good_years": 0.6,
         "deflated_sharpe": 0.95, "random_percentile": 95.0}


def load_log() -> list:
    return json.loads(LOG.read_text(encoding="utf-8")) if LOG.exists() else []


def fingerprint(name: str) -> str:
    path = Path(__file__).resolve().parent / "tactics" / f"{name}.py"
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:16]


def deflated_sharpe(returns: np.ndarray, trials: int) -> float:
    """Chance the true Sharpe beats the best of `trials` lucky tries (Bailey and Lopez de Prado)."""
    r = returns[returns != 0]
    n, sr = len(r), r.mean() / r.std(ddof=1)
    z = (r - r.mean()) / r.std(ddof=1)
    skew, kurt = (z**3).mean(), (z**4).mean()
    gamma = 0.5772156649
    expected = 0.0
    if trials > 1:
        # The spread of Sharpe across tries is unknown with few tries, so use one standard error
        se = 1 / math.sqrt(n)
        expected = se * ((1 - gamma) * norm.inv_cdf(1 - 1 / trials) + gamma * norm.inv_cdf(1 - 1 / (trials * math.e)))
    return float(norm.cdf((sr - expected) * math.sqrt(n - 1) / math.sqrt(1 - skew * sr + (kurt - 1) / 4 * sr**2)))


def look_ahead_check(days, tactic) -> bool:
    """Levels for a day and minute must not change when the future is removed or altered."""
    full = tactic.levels(days, tactic.SETTINGS)
    d, m = len(days.dates) - 50, 200
    altered = type(days)(days.dates, days.open.copy(), days.high.copy(), days.low.copy(), days.close.copy(),
                         days.ticks.copy(), days.spread.copy())
    for table in (altered.open, altered.high, altered.low, altered.close, altered.ticks):
        table[d, m + 1:] *= 1.5
        table[d + 1:] *= 1.5
    changed = tactic.levels(altered, tactic.SETTINGS)
    same = (np.isclose(full.upper[d, m], changed.upper[d, m]) and np.isclose(full.lower[d, m], changed.lower[d, m])
            and np.isclose(full.vwap[d, m], changed.vwap[d, m]))
    return bool(same)


def evaluate(name: str, include_holdout: bool, trials: int) -> dict:
    tactic = importlib.import_module(f"ftmo.tactics.{name}")
    days = load_days(SYMBOL, include_holdout)
    base = run(days, tactic, tactic.SETTINGS, TEST_VOL)
    s = sharpe(base.returns)

    stability = []
    for key in tactic.TUNABLE:
        for factor in (0.8, 1.2):
            changed = dict(tactic.SETTINGS, **{key: tactic.SETTINGS[key] * factor})
            stability.append(sharpe(run(days, tactic, changed, TEST_VOL).returns))

    years = {}
    for year in sorted(set(base.dates.year)):
        picked = base.returns[base.dates.year == year]
        years[int(year)] = round(float(np.prod(1 + picked) - 1) * 100, 2)

    randoms = [sharpe(run(days, tactic, tactic.SETTINGS, TEST_VOL, random_seed=i).returns) for i in range(RANDOM_RUNS)]
    other = run(load_days("US500.cash", include_holdout), tactic, tactic.SETTINGS, TEST_VOL)

    measured = {
        "trades": base.trades, "sharpe": s,
        "double_cost_sharpe": sharpe(run(days, tactic, tactic.SETTINGS, TEST_VOL, cost_mult=2).returns),
        "stability_gap": max(abs(s - x) for x in stability),
        "good_years": sum(v > 0 for v in years.values()) / len(years),
        "deflated_sharpe": deflated_sharpe(base.returns, trials),
        "random_percentile": float((np.array(randoms) < s).mean() * 100),
    }
    passed = {k: (measured[k] <= g if k == "stability_gap" else measured[k] >= g) for k, g in GATES.items()}
    equity = np.cumprod(1 + base.returns)
    return {
        "measured": {k: round(v, 3) for k, v in measured.items()}, "passed": passed, "all_passed": all(passed.values()),
        "total_return_pct": round(float(equity[-1] - 1) * 100, 2),
        "max_drawdown_pct": round(float((equity / np.maximum.accumulate(equity) - 1).min()) * 100, 2),
        "worst_day_pct": round(float(base.worst.min()) * 100, 2),
        "active_days": int((base.returns != 0).sum()), "years_pct": years,
        "random_sharpe_median": round(float(np.median(randoms)), 3),
        "us500_sharpe_info_only": round(sharpe(other.returns), 3),
        "challenge_odds": {f"{v:.1%}_daily_vol": challenge_odds(run(days, tactic, tactic.SETTINGS, v)) for v in CHALLENGE_VOLS},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["board", "test", "final"])
    parser.add_argument("name", nargs="?")
    args = parser.parse_args()
    log = load_log()

    if args.command == "board":
        print(f"Tactics used {len({e['name'] for e in log})} of {MAX_TACTICS}")
        for entry in log:
            print(entry["when"], entry["kind"], entry["name"], "PASS" if entry["result"]["all_passed"] else "FAIL")
        return

    print_me = fingerprint(args.name)
    tested = [e for e in log if e["name"] == args.name and e["kind"] == "test"]
    if args.command == "test":
        if any(e["fingerprint"] == print_me for e in log):
            raise SystemExit("Refused: this version has already had its one test.")
        if tested:
            raise SystemExit("Refused: changing a tested tactic means a new tactic and a new name.")
        if len({e["name"] for e in log}) >= MAX_TACTICS:
            raise SystemExit("Refused: tactic budget is used up.")
        if not look_ahead_check(load_days(SYMBOL), importlib.import_module(f"ftmo.tactics.{args.name}")):
            raise SystemExit("Refused: the tactic looks into the future.")
    else:
        passes = [e for e in tested if e["fingerprint"] == print_me and e["result"]["all_passed"]]
        if not passes or any(e["name"] == args.name and e["kind"] == "final" for e in log):
            raise SystemExit("Refused: final needs a passed test, and runs once.")

    trials = max(1, len({e["name"] for e in log} | {args.name}))
    result = evaluate(args.name, args.command == "final", trials)
    log.append({"when": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": args.command, "name": args.name,
                "fingerprint": print_me, "result": result})
    LOG.write_text(json.dumps(log, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
