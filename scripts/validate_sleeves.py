"""Validation report for the core + satellite plan (strategy-risk-spec.md section 7 gates).

    python scripts/validate_sleeves.py

Core       config/core.yaml       daily Donchian ensemble, 6 coins, shorts, aggressive budget
Satellite  config/satellite.yaml  4h pullback + mean reversion, BTC/ETH/SOL
Combined   60% core + 40% satellite, separate accounts (no money moved between them)

For each sleeve a FIXED list of variants (decided before any result was seen) is run:
  "sel:"   candidates the walk-forward may choose from (train 3y -> test next 6m, rolled)
  "+-20%:" neighbours of the default, for the stability gate
Every variant is added to data/variants_log.csv, and the Deflated Sharpe counts all of them.

Gate 3b uses the owner's budget: max drawdown must stay under the -35% kill switch,
and the kill switch must never have fired in the backtest.
"""

from __future__ import annotations

import csv
import dataclasses
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from cryptobot.backtest.data import MarketDataStore
from cryptobot.backtest.engine import Backtester, BacktestConfig, daily_equity
from cryptobot.backtest.loading import load_history
from cryptobot.backtest.validation import (annual_sharpe, deflated_sharpe_probability, max_drawdown,
                                           walk_forward)
from cryptobot.strategies.donchian_portfolio import DonchianPortfolio, load_overlay_config, load_strategy_config
from cryptobot.strategies.satellite import SatellitePortfolio, load_satellite_config

LOG = Path("data/variants_log.csv")
INITIAL = 20_000


def round_trips(fills: pd.DataFrame) -> int:
    n = 0
    for _, f in fills.groupby("symbol") if not fills.empty else []:
        pos = 0.0
        for q in f["qty"]:
            pos = round(pos + q, 12)
            n += pos == 0
    return n


def run_core() -> tuple[dict, dict, list[str]]:
    strat, risk, raw = load_strategy_config("config/core.yaml")
    overlay_cfg = load_overlay_config(raw)
    hist = load_history(raw["symbols"]["core"], extended=True)
    costs = raw["costs"]
    bt = BacktestConfig(initial_equity=INITIAL * raw["capital_share"], taker_fee=costs["taker"],
                        slippage_bps=dict(costs["slippage_bps"]))
    scaled = lambda f: tuple(max(2, round(L * f)) for L in strat.lookbacks)  # noqa: E731
    variants = {
        "sel: core DEFAULT": (strat, risk),
        "sel: core longs only": (dataclasses.replace(strat, allow_shorts=False), risk),
        "sel: core stop 2.4 ATR": (strat, dataclasses.replace(risk, k_atr=2.4)),
        "sel: core stop 3.6 ATR": (strat, dataclasses.replace(risk, k_atr=3.6)),
        "+-20%: core risk per trade 1.2%": (strat, dataclasses.replace(risk, risk_per_trade=0.012)),
        "+-20%: core risk per trade 1.8%": (strat, dataclasses.replace(risk, risk_per_trade=0.018)),
        "+-20%: core vol target 32%": (strat, dataclasses.replace(risk, vol_target_asset=0.32)),
        "+-20%: core vol target 48%": (strat, dataclasses.replace(risk, vol_target_asset=0.48)),
        "+-20%: core lookbacks x0.8": (dataclasses.replace(strat, lookbacks=scaled(0.8)), risk),
        "+-20%: core lookbacks x1.2": (dataclasses.replace(strat, lookbacks=scaled(1.2)), risk),
    }
    results, rets = {}, {}
    for name, (s_cfg, r_cfg) in variants.items():
        strategy = DonchianPortfolio(hist.bars, s_cfg, r_cfg, funding=hist.funding, overlay_cfg=overlay_cfg,
                                     btc_bars=hist.btc)
        res = Backtester(bt, hist.bars, hist.funding, hist.marks).run(strategy)
        results[name], rets[name] = _summarise(name, res, strategy.breakers.stopped)
    return results, rets, hist.notes


def run_satellite() -> tuple[dict, dict, list[str]]:
    sat, risk, raw = load_satellite_config("config/satellite.yaml")
    symbols = raw["symbols"]["satellite"]
    store = MarketDataStore("data/market.sqlite")
    try:
        bars = {s: store.bars(s, "trade_4h") for s in symbols}
        marks = {s: store.bars(s, "mark_4h") for s in symbols}
        funding = {s: store.funding(s) for s in symbols}
    finally:
        store.close()
    if any(df.empty for df in bars.values()):
        raise RuntimeError("No 4h data - run: python scripts/download_history.py BTCUSDT ETHUSDT SOLUSDT --intraday 4h")
    costs = raw["costs"]
    bt = BacktestConfig(initial_equity=INITIAL * raw["capital_share"], taker_fee=costs["taker"],
                        slippage_bps=dict(costs["slippage_bps"]), bar=pd.Timedelta(hours=4))
    variants = {
        "sel: sat DEFAULT (pullback + meanrev)": (sat, risk),
        "sel: sat pullback only": (dataclasses.replace(sat, tactics=("pullback",)), risk),
        "sel: sat meanrev only": (dataclasses.replace(sat, tactics=("meanrev",)), risk),
        "+-20%: sat risk per trade 0.8%": (sat, dataclasses.replace(risk, risk_per_trade=0.008)),
        "+-20%: sat risk per trade 1.2%": (sat, dataclasses.replace(risk, risk_per_trade=0.012)),
        "+-20%: sat trend stop 1.6 ATR": (dataclasses.replace(sat, trend_stop_atr=1.6), risk),
        "+-20%: sat trend stop 2.4 ATR": (dataclasses.replace(sat, trend_stop_atr=2.4), risk),
        "+-20%: sat trail 2.4 ATR": (dataclasses.replace(sat, trend_trail_atr=2.4), risk),
        "+-20%: sat trail 3.6 ATR": (dataclasses.replace(sat, trend_trail_atr=3.6), risk),
        "+-20%: sat bands 1.6 std": (dataclasses.replace(sat, bb_std=1.6), risk),
        "+-20%: sat bands 2.4 std": (dataclasses.replace(sat, bb_std=2.4), risk),
    }
    results, rets = {}, {}
    for name, (s_cfg, r_cfg) in variants.items():
        strategy = SatellitePortfolio(bars, s_cfg, r_cfg, funding=funding)
        res = Backtester(bt, bars, funding, marks).run(strategy)
        results[name], rets[name] = _summarise(name, res, strategy.breakers.stopped)
    notes = [f"{s} 4h futures candles {df.index.min():%Y-%m-%d} -> {df.index.max():%Y-%m-%d}" for s, df in bars.items()]
    return results, rets, notes


def _summarise(name, res, killed) -> tuple[dict, pd.Series]:
    eq = daily_equity(res.equity)
    r = eq.pct_change().fillna(0.0)
    m = res.metrics()
    out = {"sharpe": annual_sharpe(r), "max_dd": max_drawdown(r), "cagr": m["cagr"], "vol": m["vol"],
           "trips": round_trips(res.fills), "killed": killed, "fees": res.totals["fees"],
           "funding": res.totals["funding"], "final": eq.iloc[-1]}
    print(f"  ran {name:<42} Sharpe {out['sharpe']:5.2f}  CAGR {out['cagr']:6.1%}  maxDD {out['max_dd']:6.1%}  "
          f"trades {out['trips']:4d}{'  KILL SWITCH' if killed else ''}", flush=True)
    return out, r


def gates(label, results, rets, trial_daily, default, dd_limit=0.35):
    candidates = {k: v for k, v in rets.items() if k.startswith("sel:")}
    wf = walk_forward(candidates)
    oos = wf.oos_returns
    d = results[default]
    base = d["sharpe"]
    neighbours = {k: v["sharpe"] for k, v in results.items() if k.startswith("+-20%")}
    gap = max(abs(s - base) for s in neighbours.values())
    dsr = deflated_sharpe_probability(oos, trial_daily)
    rows = [
        (f"{label} 1 Walk-forward (train 3y / test 6m)", f"{len(wf.windows)} test windows", len(wf.windows) >= 4),
        (f"{label} 2 At least 100 trades", f"{d['trips']} round trips (default)", d["trips"] >= 100),
        (f"{label} 3a Out-of-sample Sharpe > 0.8", f"{annual_sharpe(oos):.2f}", annual_sharpe(oos) > 0.8),
        (f"{label} 3b Max drawdown < {dd_limit:.0%}, kill switch never hit",
         f"{d['max_dd']:.1%}{', KILL SWITCH FIRED' if d['killed'] else ''}",
         d["max_dd"] > -dd_limit and not d["killed"]),
        (f"{label} 4 Stable at +-20%", f"Sharpe {base:.2f}, neighbours {min(neighbours.values()):.2f}.."
         f"{max(neighbours.values()):.2f} (gap {gap:.2f}, pass <= 0.25)", gap <= 0.25),
        (f"{label} 5 Deflated Sharpe > 0.95", f"{dsr:.2f} ({len(trial_daily)} variants counted)", dsr > 0.95),
    ]
    return rows, wf, oos


def main() -> int:
    print("Core sleeve (daily):")
    core_res, core_rets, core_notes = run_core()
    print("Satellite sleeve (4h):")
    sat_res, sat_rets, sat_notes = run_satellite()

    previous = _logged_sharpes()
    every = list(core_res.values()) + list(sat_res.values())
    trial_daily = [r["sharpe"] / 365 ** 0.5 for r in every] + [s / 365 ** 0.5 for s in previous]
    core_default, sat_default = "sel: core DEFAULT", "sel: sat DEFAULT (pullback + meanrev)"
    core_gates, core_wf, core_oos = gates("Core", core_res, core_rets, trial_daily, core_default)
    sat_gates, sat_wf, sat_oos = gates("Satellite", sat_res, sat_rets, trial_daily, sat_default)

    # Combined account: each sleeve on its own capital, so the total return per day is the
    # capital-weighted mix of the two sleeves' returns (weights drift as the sleeves grow).
    def combine(a: pd.Series, b: pd.Series) -> pd.Series:
        common = a.index.intersection(b.index)
        ea, eb = 0.6 * (1 + a[common]).cumprod(), 0.4 * (1 + b[common]).cumprod()
        total = ea + eb
        return total.pct_change().fillna(total.iloc[0] - 1)
    full = combine(core_rets[core_default], sat_rets[sat_default])
    oos = combine(core_oos, sat_oos)
    corr = core_rets[core_default].corr(sat_rets[sat_default])
    dsr = deflated_sharpe_probability(oos, trial_daily)
    combo_gates = [
        ("Combined 3a Out-of-sample Sharpe > 0.8", f"{annual_sharpe(oos):.2f} ({oos.index.min():%Y-%m} on)",
         annual_sharpe(oos) > 0.8),
        ("Combined 3b Max drawdown < 35% (full period, defaults)", f"{max_drawdown(full):.1%}",
         max_drawdown(full) > -0.35),
        ("Combined 5 Deflated Sharpe > 0.95", f"{dsr:.2f}", dsr > 0.95),
    ]
    all_gates = core_gates + sat_gates + combo_gates
    text = _report(core_notes + sat_notes, core_res, sat_res, core_wf, sat_wf, all_gates, full, oos, corr)
    print("\n" + text)
    out = Path("reports") / f"validation-sleeves-{datetime.now(timezone.utc):%Y-%m-%d}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(text, encoding="utf-8")
    _log({**core_res, **sat_res})
    print(f"\nSaved {out}; logged {len(core_res) + len(sat_res)} variants to {LOG}")
    return 0


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
            w.writerow([datetime.now(timezone.utc).isoformat(), f"sleeves: {name}", round(r["final"], 2),
                        round(r["cagr"], 4), round(r["sharpe"], 3), round(r["max_dd"], 4), round(r["vol"], 4),
                        round(r["fees"], 2), round(r["funding"], 2)])


def _report(notes, core_res, sat_res, core_wf, sat_wf, gates_, full, oos, corr) -> str:
    lines = [f"# Core + satellite validation {datetime.now(timezone.utc):%Y-%m-%d}", "", "## Data", ""]
    lines += [f"- {n}" for n in notes]
    lines += ["", "## Gates (strategy-risk-spec.md section 7, owner's -35% budget for 3b)", "",
              "| Gate | Result | Pass |", "|---|---|---|"]
    lines += [f"| {g} | {v} | {'PASS' if ok else 'FAIL'} |" for g, v, ok in gates_]
    lines += ["", f"**Overall: {'ALL GATES PASS' if all(ok for *_, ok in gates_) else 'NOT READY FOR REAL MONEY'}**",
              "", f"Combined (60/40, full period, defaults): Sharpe {annual_sharpe(full):.2f}, max drawdown "
              f"{max_drawdown(full):.1%}, total return {(1 + full).prod() - 1:+.1%}. "
              f"Daily correlation core vs satellite: {corr:.2f}.",
              f"Combined out-of-sample (walk-forward picks): Sharpe {annual_sharpe(oos):.2f}, max drawdown "
              f"{max_drawdown(oos):.1%}, total return {(1 + oos).prod() - 1:+.1%}."]
    for label, wf in (("Core", core_wf), ("Satellite", sat_wf)):
        lines += ["", f"## {label} walk-forward windows", "",
                  "| Train | Test | Chosen | Train Sharpe | Test Sharpe | Test return |", "|---|---|---|---|---|---|"]
        lines += [f"| {w['train']} | {w['test']} | {w['chosen'].replace('sel: ', '')} | {w['train_sharpe']:.2f} | "
                  f"{w['test_sharpe']:.2f} | {w['test_return']:+.1%} |" for w in wf.windows]
    lines += ["", "## All variants (full period)", "",
              "| Variant | Sharpe | CAGR | Max DD | Vol | Trades | Kill switch |", "|---|---|---|---|---|---|---|"]
    lines += [f"| {k} | {v['sharpe']:.2f} | {v['cagr']:.1%} | {v['max_dd']:.1%} | {v['vol']:.1%} | {v['trips']} | "
              f"{'FIRED' if v['killed'] else '-'} |" for k, v in {**core_res, **sat_res}.items()]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
