"""The research lab. Its rules live in code so that neither people nor agents can bend them.

    python -m daytrader.lab test NAME      # research test of src/daytrader/tactics/NAME.py, once
    python -m daytrader.lab final NAME     # hidden-data test, once, only after passing `test`
    python -m daytrader.lab board          # everything tried so far

Rules enforced here:
  - Budget: at most `MAX_TACTICS` tactics, so the search can't run until luck finds something.
  - One shot: a tactic file is fingerprinted. Changing it means a new name, which uses budget.
  - Hidden data: everything from HOLDOUT_START on is invisible to `test`, and `final` runs
    once per tactic.
  - Code check: a tactic may only use numpy, pandas and our indicators, and no negative shifts.
  - Look-ahead check: signals must not change when later data is removed.
  - Every attempt is logged, and the skill-or-luck score counts all of them.
"""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from cryptobot.backtest.data import MarketDataStore
from cryptobot.backtest.validation import annual_sharpe, deflated_sharpe_probability, max_drawdown
from daytrader import sim

DB = "data/daytrade.sqlite"
LOG = Path("data/tactics_log.csv")
REPORTS = Path("reports/tactics")
TACTICS = Path(__file__).parent / "tactics"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
HOLDOUT_START = pd.Timestamp("2025-05-01", tz="UTC")
MAX_TACTICS = 12            # raising this is the owner's decision
ALLOWED_IMPORTS = {"numpy", "pandas", "cryptobot.indicators", "cryptobot", "__future__"}
BANNED_CALLS = {"open", "exec", "eval", "compile", "__import__", "getattr", "setattr", "globals", "locals"}
COLUMNS = ["time", "name", "hash", "stage", "sharpe", "sharpe_cost2", "max_dd", "ret_year", "trades", "passed"]

MIN_TRADES = 300
MIN_SHARPE = 0.8
MIN_SHARPE_COSTS = 0.4
MAX_GAP = 0.25
MIN_GOOD_YEARS = 0.6
MIN_DSR = 0.95
MIN_FINAL_SHARPE = 0.5


class LabRefused(RuntimeError):
    pass


# ---- data ---------------------------------------------------------------------------------

def load(db: str = DB) -> dict:
    store = MarketDataStore(db)
    try:
        data = {}
        for s in SYMBOLS:
            bars = store.bars(s, "trade_1h")
            if bars.empty:
                raise LabRefused(f"No 1h data for {s}: run scripts/download_history.py {s} --db {db} --intraday 1h")
            funding = store.funding(s)
            bars["funding"] = funding["rate"].groupby(funding.index.floor("h")).sum().reindex(bars.index).ffill().fillna(0.0)
            data[s] = {"bars": bars, "mark": store.bars(s, "mark_1h"), "funding": funding["rate"]}
    finally:
        store.close()
    btc = data["BTCUSDT"]["bars"]["close"]
    for d in data.values():
        d["bars"]["btc_close"] = btc.reindex(d["bars"].index).ffill()
    return data


def part(data: dict, start=None, end=None) -> dict:
    def cut(x):
        return x if x is None else x.loc[start:end]
    return {s: {k: cut(v) for k, v in d.items()} for s, d in data.items()}


def research_part(data: dict) -> dict:
    return part(data, end=HOLDOUT_START - pd.Timedelta(hours=1))


# ---- checks -------------------------------------------------------------------------------

def check_code(path: Path) -> None:
    """A tactic is pure maths: no files, no network, no peeking."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            names = []
        for n in names:
            if n not in ALLOWED_IMPORTS:
                raise LabRefused(f"{path.name}: import of {n} is not allowed")
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id in BANNED_CALLS:
                raise LabRefused(f"{path.name}: {f.id}() is not allowed")
            if isinstance(f, ast.Attribute) and f.attr == "shift":
                for arg in list(node.args) + [k.value for k in node.keywords]:
                    if isinstance(arg, ast.UnaryOp) and isinstance(arg.op, ast.USub):
                        raise LabRefused(f"{path.name}: a negative shift looks into the future")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise LabRefused(f"{path.name}: {node.attr} is not allowed")


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def get_tactic(name: str):
    if not name.isidentifier():
        raise LabRefused(f"Bad tactic name {name!r}")
    path = TACTICS / f"{name}.py"
    if not path.exists():
        raise LabRefused(f"No tactic file {path}")
    check_code(path)
    module = importlib.import_module(f"daytrader.tactics.{name}")
    for attr in ("HYPOTHESIS", "SETTINGS", "TUNABLE", "signals"):
        if not hasattr(module, attr):
            raise LabRefused(f"{name} must define {attr}")
    return module, path


def signals_for(module, data: dict, settings: dict) -> dict[str, pd.DataFrame]:
    out = {}
    for s, d in data.items():
        sig = module.signals(d["bars"], settings)
        missing = {"side", "stop", "target", "max_bars"} - set(sig.columns)
        if missing:
            raise LabRefused(f"signals() is missing {sorted(missing)}")
        if not sig.index.equals(d["bars"].index):
            raise LabRefused("signals() must keep the bars' index")
        out[s] = sig
    return out


def check_no_lookahead(module, data: dict) -> None:
    """Signals up to a date must not change when everything after that date is removed."""
    bars = data["BTCUSDT"]["bars"]
    full = module.signals(bars, module.SETTINGS)
    for frac in (0.4, 0.6, 0.8):
        cut = bars.index[int(len(bars) * frac)]
        short = module.signals(bars.loc[:cut], module.SETTINGS)
        a = full.loc[:cut, ["side", "stop", "target", "max_bars"]].to_numpy(float)
        b = short[["side", "stop", "target", "max_bars"]].to_numpy(float)
        if not np.allclose(np.nan_to_num(a, nan=-9e9), np.nan_to_num(b, nan=-9e9), rtol=1e-6, atol=1e-9):
            raise LabRefused("Look-ahead: signals change when later data is removed")


# ---- log ----------------------------------------------------------------------------------

def read_log(log: Path = LOG) -> list[dict]:
    if not log.exists():
        return []
    with log.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_log(row: dict, log: Path = LOG) -> None:
    new = not log.exists()
    log.parent.mkdir(exist_ok=True)
    with log.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, COLUMNS)
        if new:
            w.writeheader()
        w.writerow(row)


def check_budget(name: str, digest: str, rows: list[dict]) -> None:
    mine = [r for r in rows if r["name"] == name]
    if mine:
        same = any(r["hash"] == digest for r in mine)
        raise LabRefused(f"{name} was already tested." if same else
                         f"{name} changed after its test. Changing rules means a new tactic, under a new name.")
    tried = {r["name"] for r in rows}
    if len(tried) >= MAX_TACTICS:
        raise LabRefused(f"Budget used: {len(tried)} of {MAX_TACTICS} tactics. Only the owner can raise it.")


# ---- scoring ------------------------------------------------------------------------------

def score(res: sim.Result) -> dict:
    d = res.daily
    t = res.trades
    return {"sharpe": annual_sharpe(d), "max_dd": max_drawdown(d), "ret_year": float(d.mean() * 365), "trades": len(t),
            "win_rate": float((t["net"] > 0).mean()) if len(t) else 0.0, "avg_r": float(t["r"].mean()) if len(t) else 0.0}


def good_years(daily: pd.Series) -> float:
    years = daily.groupby(daily.index.year).sum()
    return float((years > 0).mean())


def neighbours(module) -> dict[str, dict]:
    """Each tunable setting moved up and down 20%."""
    out = {}
    for key in module.TUNABLE:
        base = module.SETTINGS[key]
        for f in (0.8, 1.2):
            value = base * f
            value = max(2, round(value)) if isinstance(base, int) else value
            out[f"{key} {value}"] = {**module.SETTINGS, key: value}
    return out


def trials(rows: list[dict], extra: list[float]) -> list[float]:
    """Daily Sharpe of every attempt: old tests, this one, and the research behind it."""
    old = [float(r["sharpe"]) for r in rows if r["stage"] == "test"]
    return [s / 365 ** 0.5 for s in old + extra]


def run_test(name: str, data: dict | None = None, log: Path = LOG, reports: Path = REPORTS) -> dict:
    module, path = get_tactic(name)
    digest = fingerprint(path)
    rows = read_log(log)
    check_budget(name, digest, rows)
    data = research_part(data or load())
    check_no_lookahead(module, data)

    base = sim.run(data, signals_for(module, data, module.SETTINGS))
    stats = score(base)
    stats2 = score(sim.run(data, signals_for(module, data, module.SETTINGS), cost_mult=2.0))
    near = {k: score(sim.run(data, signals_for(module, data, s)))["sharpe"] for k, s in neighbours(module).items()}
    gap = max((abs(v - stats["sharpe"]) for v in near.values()), default=0.0)
    years = good_years(base.daily)
    dsr = deflated_sharpe_probability(base.daily, trials(rows, [stats["sharpe"], *near.values()]))
    gates = [
        (f"At least {MIN_TRADES} trades", f"{stats['trades']}", stats["trades"] >= MIN_TRADES),
        (f"Net Sharpe above {MIN_SHARPE}", f"{stats['sharpe']:.2f}", stats["sharpe"] > MIN_SHARPE),
        (f"Sharpe above {MIN_SHARPE_COSTS} with double costs", f"{stats2['sharpe']:.2f}", stats2["sharpe"] > MIN_SHARPE_COSTS),
        (f"Stable at +-20% (gap under {MAX_GAP})", f"{gap:.2f}", gap <= MAX_GAP),
        (f"Profitable in {MIN_GOOD_YEARS:.0%} of years", f"{years:.0%}", years >= MIN_GOOD_YEARS),
        (f"Skill-or-luck score above {MIN_DSR}", f"{dsr:.2f}", dsr > MIN_DSR),
    ]
    passed = all(ok for *_, ok in gates)
    write_log({"time": datetime.now(timezone.utc).isoformat(), "name": name, "hash": digest, "stage": "test",
               "sharpe": round(stats["sharpe"], 3), "sharpe_cost2": round(stats2["sharpe"], 3),
               "max_dd": round(stats["max_dd"], 4), "ret_year": round(stats["ret_year"], 4),
               "trades": stats["trades"], "passed": passed}, log)
    text = _report(name, module, stats, stats2, near, gates, base, passed)
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"{name}.md").write_text(text, encoding="utf-8")
    return {"passed": passed, "stats": stats, "text": text}


def run_final(name: str, data: dict | None = None, log: Path = LOG, reports: Path = REPORTS) -> dict:
    module, path = get_tactic(name)
    rows = read_log(log)
    mine = [r for r in rows if r["name"] == name]
    if not any(r["stage"] == "test" and r["passed"] == "True" and r["hash"] == fingerprint(path) for r in mine):
        raise LabRefused(f"{name} must pass `test` first, unchanged.")
    if any(r["stage"] == "final" for r in mine):
        raise LabRefused(f"{name} already used its one look at the hidden data.")
    data = data or load()
    warm = HOLDOUT_START - pd.Timedelta(days=120)       # indicators need history before the hidden period
    sigs = signals_for(module, part(data, start=warm), module.SETTINGS)
    hidden = part(data, start=HOLDOUT_START)
    res = sim.run(hidden, {s: sigs[s].loc[HOLDOUT_START:] for s in hidden})
    stats = score(res)
    passed = stats["sharpe"] > MIN_FINAL_SHARPE and stats["max_dd"] > -0.15
    write_log({"time": datetime.now(timezone.utc).isoformat(), "name": name, "hash": fingerprint(path), "stage": "final",
               "sharpe": round(stats["sharpe"], 3), "sharpe_cost2": "", "max_dd": round(stats["max_dd"], 4),
               "ret_year": round(stats["ret_year"], 4), "trades": stats["trades"], "passed": passed}, log)
    text = (f"# {name}: hidden-data result\n\nSharpe {stats['sharpe']:.2f}, worst drop {stats['max_dd']:.1%}, "
            f"{stats['ret_year']:+.1%}/yr, {stats['trades']} trades. Pass: {passed}\n")
    with (reports / f"{name}.md").open("a", encoding="utf-8") as f:
        f.write("\n" + text)
    return {"passed": passed, "stats": stats, "text": text}


def _report(name, module, stats, stats2, near, gates, base, passed) -> str:
    t = base.trades
    reasons = t["reason"].value_counts().to_dict() if len(t) else {}
    years = base.daily.groupby(base.daily.index.year).sum()
    lines = [f"# {name}", "", f"**Idea:** {module.HYPOTHESIS}", "", f"**Settings:** {module.SETTINGS}", "",
             f"**Result: {'PASSED, may take the final test' if passed else 'FAILED'}**", "",
             "| Gate | Result | Pass |", "|---|---|---|"]
    lines += [f"| {g} | {v} | {'PASS' if ok else 'FAIL'} |" for g, v, ok in gates]
    lines += ["", f"Research period return {stats['ret_year']:+.1%}/yr at 0.5% risk per trade, worst drop "
              f"{stats['max_dd']:.1%}, win rate {stats['win_rate']:.0%}, average trade {stats['avg_r']:+.2f}R.",
              f"With double costs: Sharpe {stats2['sharpe']:.2f}. Exits: {reasons}.", "", "| Year | Return |", "|---|---|"]
    lines += [f"| {y} | {v:+.1%} |" for y, v in years.items()]
    lines += ["", "| +-20% change | Sharpe |", "|---|---|"]
    lines += [f"| {k} | {v:.2f} |" for k, v in near.items()]
    return "\n".join(lines) + "\n"


def board(log: Path = LOG) -> str:
    rows = read_log(log)
    used = len({r["name"] for r in rows})
    lines = [f"{used} of {MAX_TACTICS} tactics used. Hidden data starts {HOLDOUT_START:%Y-%m-%d}."]
    lines += [f"{r['stage']:<5} {r['name']:<28} Sharpe {r['sharpe']:>6} worst {r['max_dd']:>7} "
              f"trades {r['trades']:>5} pass {r['passed']}" for r in rows]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m daytrader.lab")
    parser.add_argument("command", choices=["test", "final", "board"])
    parser.add_argument("name", nargs="?")
    args = parser.parse_args()
    try:
        if args.command == "board":
            print(board())
        elif not args.name:
            parser.error("give a tactic name")
        else:
            print((run_test if args.command == "test" else run_final)(args.name)["text"])
    except LabRefused as e:
        print(f"REFUSED: {e}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
