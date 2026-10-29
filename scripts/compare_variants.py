"""Run several risk settings side by side on the same history (diagnostics).

    python scripts/compare_variants.py

Every variant run here is printed and appended to data/variants_log.csv:
strategy-risk-spec.md section 7 requires logging every variant tried, because
trying many settings and keeping the best one makes backtests look better than
reality (that is what the Deflated Sharpe check in step 6 corrects for).
"""

from __future__ import annotations

import csv
import dataclasses
import sys
from datetime import datetime, timezone
from pathlib import Path

from cryptobot.backtest.data import MarketDataStore
from cryptobot.backtest.engine import Backtester, BacktestConfig, buy_and_hold
from cryptobot.strategies.donchian_portfolio import DonchianPortfolio, load_strategy_config

UNCAPPED = dict(max_heat=1.0, cluster_heat=1.0, max_net_notional=1.5)   # only the vol target + 1.5x leverage cap


def main() -> int:
    strat, risk, raw = load_strategy_config()
    symbols = raw["symbols"]["core"]
    store = MarketDataStore()
    bars = {s: store.bars(s, "trade") for s in symbols}
    marks = {s: store.bars(s, "mark") for s in symbols}
    funding = {s: store.funding(s) for s in symbols}
    store.close()
    costs = raw["costs"]
    bt = BacktestConfig(initial_equity=20_000, taker_fee=costs["taker"], slippage_bps=dict(costs["slippage_bps"]))

    variants = [
        ("spec default r=0.5%", strat, risk),
        ("live ramp r=0.25%", strat, dataclasses.replace(risk, risk_per_trade=0.0025)),
        ("r=1%", strat, dataclasses.replace(risk, risk_per_trade=0.01)),
        ("r=2% (heat/cluster caps bind)", strat, dataclasses.replace(risk, risk_per_trade=0.02)),
        ("vol target only (like the paper)", strat, dataclasses.replace(risk, risk_per_trade=1.0, **UNCAPPED)),
        ("spec default + shorts", dataclasses.replace(strat, allow_shorts=True), risk),
    ]
    rows = []
    print(f"{'variant':<36} {'final':>9} {'CAGR':>7} {'Sharpe':>7} {'maxDD':>7} {'vol':>7} "
          f"{'fees':>6} {'funding':>8} {'trips':>6}")
    for name, s_cfg, r_cfg in variants:
        res = Backtester(bt, bars, funding, marks).run(DonchianPortfolio(bars, s_cfg, r_cfg))
        rows.append(_report(name, res))
    res = Backtester(bt, bars, funding, marks).run(buy_and_hold(fraction=0.5))
    rows.append(_report("buy & hold 0.5x (reference)", res))

    log = Path("data/variants_log.csv")
    new = not log.exists()
    with log.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["run_at", "variant", "final", "cagr", "sharpe", "max_dd", "vol", "fees", "funding"])
        for r in rows:
            w.writerow([datetime.now(timezone.utc).isoformat(), *r])
    print(f"\n(logged {len(rows)} variants to {log})")
    return 0


def _report(name, res):
    m, t = res.metrics(), res.totals
    trips = int((res.fills["reason"] != "signal").sum()) if not res.fills.empty else 0
    closes = 0
    if not res.fills.empty:
        for _, f in res.fills.groupby("symbol"):
            pos = 0.0
            for q in f["qty"]:
                pos = round(pos + q, 12)
                closes += pos == 0
    liq = " LIQUIDATED" if t.get("liquidated") else ""
    print(f"{name:<36} {m['final_equity']:>9,.0f} {m['cagr']:>7.1%} {m['sharpe']:>7.2f} {m['max_drawdown']:>7.1%} "
          f"{m['vol']:>7.1%} {t['fees']:>6,.0f} {t['funding']:>8,.0f} {closes:>6}{liq}")
    return [name, round(m["final_equity"]), round(m["cagr"], 4), round(m["sharpe"], 3), round(m["max_drawdown"], 4),
            round(m["vol"], 4), round(t["fees"], 2), round(t["funding"], 2)]


if __name__ == "__main__":
    sys.exit(main())
