"""Validation report - strategy-risk-spec.md section 7 gates (build step 6).

    python scripts/validate.py              # extended history 2017-08 -> today (default)
    python scripts/validate.py --normal     # futures-only history 2020 -> today

1. Runs a small, FIXED set of variants once over the whole history (settings
   decided before looking at results - see VARIANTS below).
2. Walk-forward: train 3 years -> pick best Sharpe -> test next 6 months, rolled.
3. +-20% check around the configured default.
4. Deflated Sharpe: counts EVERY variant ever tried (this run + data/variants_log.csv).
5. Prints pass/fail per gate and saves reports/validation-<date>.md.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from cryptobot.backtest.engine import Backtester, BacktestConfig
from cryptobot.backtest.loading import load_history
from cryptobot.backtest.validation import (annual_sharpe, deflated_sharpe_probability, max_drawdown,
                                           walk_forward)
from cryptobot.strategies.donchian_portfolio import DonchianPortfolio, load_overlay_config, load_strategy_config

LOG = Path("data/variants_log.csv")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--normal", action="store_true", help="use only 2020+ futures history")
    args = parser.parse_args()

    strat, risk, raw = load_strategy_config()
    overlay_cfg = load_overlay_config(raw)
    symbols = raw["symbols"]["core"]
    hist = load_history(symbols, extended=not args.normal)
    costs = raw["costs"]
    bt = BacktestConfig(initial_equity=20_000, taker_fee=costs["taker"], slippage_bps=dict(costs["slippage_bps"]))

    vol_target = dataclasses.replace(risk, risk_per_trade=1.0, max_heat=1.0, cluster_heat=1.0, max_net_notional=1.5)
    scaled = lambda f: tuple(max(2, round(L * f)) for L in strat.lookbacks)  # noqa: E731
    # Fixed list, decided up front. "sel:" = candidates the walk-forward may choose from.
    variants = {
        "sel: spec sizing, stop 3.0 ATR (DEFAULT)": (strat, risk),
        "sel: spec sizing, stop 2.4 ATR": (strat, dataclasses.replace(risk, k_atr=2.4)),
        "sel: spec sizing, stop 3.6 ATR": (strat, dataclasses.replace(risk, k_atr=3.6)),
        "sel: spec sizing + shorts": (dataclasses.replace(strat, allow_shorts=True), risk),
        "sel: vol-target sizing, stop 3.0 ATR": (strat, vol_target),
        "sel: vol-target sizing, stop 2.4 ATR": (strat, dataclasses.replace(vol_target, k_atr=2.4)),
        "sel: vol-target sizing, stop 3.6 ATR": (strat, dataclasses.replace(vol_target, k_atr=3.6)),
        "sel: vol-target sizing + shorts": (dataclasses.replace(strat, allow_shorts=True), vol_target),
        "+-20%: risk per trade 0.4%": (strat, dataclasses.replace(risk, risk_per_trade=0.004)),
        "+-20%: risk per trade 0.6%": (strat, dataclasses.replace(risk, risk_per_trade=0.006)),
        "+-20%: vol target 12%": (strat, dataclasses.replace(risk, vol_target_asset=0.12)),
        "+-20%: vol target 18%": (strat, dataclasses.replace(risk, vol_target_asset=0.18)),
        "+-20%: lookbacks x0.8": (dataclasses.replace(strat, lookbacks=scaled(0.8)), risk),
        "+-20%: lookbacks x1.2": (dataclasses.replace(strat, lookbacks=scaled(1.2)), risk),
    }
    results, rets = {}, {}
    for name, (s_cfg, r_cfg) in variants.items():
        strategy = DonchianPortfolio(hist.bars, s_cfg, r_cfg, funding=hist.funding, overlay_cfg=overlay_cfg,
                                     btc_bars=hist.btc)
        res = Backtester(bt, hist.bars, hist.funding, hist.marks).run(strategy)
        rets[name] = res.equity.pct_change().fillna(0.0)
        results[name] = {"sharpe": annual_sharpe(rets[name]), "max_dd": max_drawdown(rets[name]),
                         "cagr": res.metrics()["cagr"], "trips": _round_trips(res.fills),
                         "vol": res.metrics()["vol"]}
        print(f"  ran {name:<44} Sharpe {results[name]['sharpe']:.2f}  maxDD {results[name]['max_dd']:.1%}",
              flush=True)

    default = "sel: spec sizing, stop 3.0 ATR (DEFAULT)"
    candidates = {k: v for k, v in rets.items() if k.startswith("sel:")}
    wf = walk_forward(candidates)
    wf_default = walk_forward({default: rets[default]})
    oos, oos_default = wf.oos_returns, wf_default.oos_returns

    previous = _logged_sharpes()
    trial_daily = [r["sharpe"] / 365 ** 0.5 for r in results.values()] + [s / 365 ** 0.5 for s in previous]
    dsr_oos = deflated_sharpe_probability(oos, trial_daily)
    dsr_default = deflated_sharpe_probability(rets[default], trial_daily)

    base = results[default]["sharpe"]
    neighbours = {k: v["sharpe"] for k, v in results.items() if k.startswith("+-20%") or "2.4 ATR" in k and "spec" in k
                  or "3.6 ATR" in k and "spec" in k}
    worst_gap = max(abs(s - base) for s in neighbours.values())
    vt = results["sel: vol-target sizing, stop 3.0 ATR"]

    gates = [
        ("1 Walk-forward done (train 3y / test 6m)", f"{len(wf.windows)} test windows", len(wf.windows) >= 4),
        ("2 At least 100 trades", f"{results[default]['trips']} round trips (default)",
         results[default]["trips"] >= 100),
        ("3a Out-of-sample net Sharpe > 0.8", f"{annual_sharpe(oos):.2f} (walk-forward choice), "
         f"{annual_sharpe(oos_default):.2f} (default only)", annual_sharpe(oos) > 0.8),
        ("3b Max drawdown < 25% at target volatility", f"{vt['max_dd']:.1%} (vol-target sizing, "
         f"{vt['vol']:.0%} vol)", vt["max_dd"] > -0.25),
        ("4 Stable when settings move +-20%", f"Sharpe {base:.2f}, neighbours "
         f"{min(neighbours.values()):.2f}..{max(neighbours.values()):.2f} (max gap {worst_gap:.2f}, pass if <= 0.25)",
         worst_gap <= 0.25),
        ("5 Deflated Sharpe probability > 0.95", f"{dsr_oos:.2f} out-of-sample, {dsr_default:.2f} default "
         f"({len(trial_daily)} variants counted)", dsr_oos > 0.95),
    ]
    text = _report(hist.notes, results, wf, gates, oos)
    print("\n" + text)
    out = Path("reports") / f"validation-{datetime.now(timezone.utc):%Y-%m-%d}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(text, encoding="utf-8")
    _log(results)
    print(f"\nSaved {out}; logged {len(results)} variants to {LOG}")
    return 0


def _round_trips(fills: pd.DataFrame) -> int:
    n = 0
    for _, f in fills.groupby("symbol") if not fills.empty else []:
        pos = 0.0
        for q in f["qty"]:
            pos = round(pos + q, 12)
            n += pos == 0
    return n


def _logged_sharpes() -> list[float]:
    if not LOG.exists():
        return []
    with LOG.open(encoding="utf-8") as f:
        return [float(r["sharpe"]) for r in csv.DictReader(f) if r.get("sharpe") and not r["variant"].startswith("buy")]


def _log(results: dict) -> None:
    new = not LOG.exists()
    with LOG.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["run_at", "variant", "final", "cagr", "sharpe", "max_dd", "vol", "fees", "funding"])
        for name, r in results.items():
            w.writerow([datetime.now(timezone.utc).isoformat(), f"validate: {name}", "", round(r["cagr"], 4),
                        round(r["sharpe"], 3), round(r["max_dd"], 4), round(r["vol"], 4), "", ""])


def _report(notes, results, wf, gates, oos) -> str:
    lines = [f"# Validation report {datetime.now(timezone.utc):%Y-%m-%d}", "", "## Data", ""]
    lines += [f"- {n}" for n in notes]
    lines += ["", "## Gates (strategy-risk-spec.md section 7)", "", "| Gate | Result | Pass |", "|---|---|---|"]
    lines += [f"| {g} | {v} | {'PASS' if ok else 'FAIL'} |" for g, v, ok in gates]
    lines += ["", f"**Overall: {'ALL GATES PASS' if all(ok for *_, ok in gates) else 'NOT READY FOR REAL MONEY'}**",
              "", "## Walk-forward windows (choice made on train, scored on unseen test)", "",
              "| Train | Test | Chosen | Train Sharpe | Test Sharpe | Test return |", "|---|---|---|---|---|---|"]
    lines += [f"| {w['train']} | {w['test']} | {w['chosen'].replace('sel: ', '')} | {w['train_sharpe']:.2f} | "
              f"{w['test_sharpe']:.2f} | {w['test_return']:+.1%} |" for w in wf.windows]
    lines += ["", f"Out-of-sample (stitched): Sharpe {annual_sharpe(oos):.2f}, max drawdown {max_drawdown(oos):.1%}, "
              f"total return {(1 + oos).prod() - 1:+.1%}", "", "## All variants (full period)", "",
              "| Variant | Sharpe | CAGR | Max DD | Vol | Trades |", "|---|---|---|---|---|---|"]
    lines += [f"| {k} | {v['sharpe']:.2f} | {v['cagr']:.1%} | {v['max_dd']:.1%} | {v['vol']:.1%} | {v['trips']} |"
              for k, v in results.items()]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
