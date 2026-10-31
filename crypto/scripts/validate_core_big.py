"""One pre-decided test of the bigger-bets core (config/core_big.yaml).

    python scripts/validate_core_big.py

No choosing between candidates: the walk-forward holds a single setting, so its test
windows are simply how that one setting did on them. +-20% neighbours are run only for
the stability gate. Every variant is logged and counted in the Deflated Sharpe.
"""

from __future__ import annotations

import dataclasses
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from validate_sleeves import INITIAL, _log, _logged_sharpes, _summarise, gates

from cryptobot.backtest.engine import Backtester, BacktestConfig
from cryptobot.backtest.loading import load_history
from cryptobot.backtest.validation import annual_sharpe, max_drawdown
from cryptobot.strategies.donchian_portfolio import DonchianPortfolio, load_overlay_config, load_strategy_config

DEFAULT = "sel: core BIG"


def main() -> int:
    strat, risk, raw = load_strategy_config("config/core_big.yaml")
    overlay_cfg = load_overlay_config(raw)
    hist = load_history(raw["symbols"]["core"], extended=True)
    bt = BacktestConfig(initial_equity=INITIAL * raw["capital_share"], taker_fee=raw["costs"]["taker"],
                        slippage_bps=dict(raw["costs"]["slippage_bps"]))
    scaled = lambda f: tuple(max(2, round(L * f)) for L in strat.lookbacks)  # noqa: E731
    variants = {
        DEFAULT: (strat, risk),
        "+-20%: big vol target 16%": (strat, dataclasses.replace(risk, vol_target_asset=0.16)),
        "+-20%: big vol target 24%": (strat, dataclasses.replace(risk, vol_target_asset=0.24)),
        "+-20%: big stop 2.4 ATR": (strat, dataclasses.replace(risk, k_atr=2.4)),
        "+-20%: big stop 3.6 ATR": (strat, dataclasses.replace(risk, k_atr=3.6)),
        "+-20%: big lookbacks x0.8": (dataclasses.replace(strat, lookbacks=scaled(0.8)), risk),
        "+-20%: big lookbacks x1.2": (dataclasses.replace(strat, lookbacks=scaled(1.2)), risk),
    }
    results, rets, default_equity, events = {}, {}, None, []
    for name, (s_cfg, r_cfg) in variants.items():
        strategy = DonchianPortfolio(hist.bars, s_cfg, r_cfg, funding=hist.funding, overlay_cfg=overlay_cfg,
                                     btc_bars=hist.btc)
        res = Backtester(bt, hist.bars, hist.funding, hist.marks).run(strategy)
        results[name], rets[name] = _summarise(name, res, strategy.breakers.stopped)
        if name == DEFAULT:
            default_equity, events = res.equity, strategy.breakers.events

    trial_daily = [r["sharpe"] / 365 ** 0.5 for r in results.values()] + [s / 365 ** 0.5 for s in _logged_sharpes()]
    rows, wf, oos = gates("Big core", results, rets, trial_daily, DEFAULT)
    d = results[DEFAULT]
    years = default_equity.groupby(default_equity.index.year).last().pct_change()
    years.iloc[0] = default_equity.groupby(default_equity.index.year).last().iloc[0] / default_equity.iloc[0] - 1

    lines = [f"# Bigger-bets core, one test {datetime.now(timezone.utc):%Y-%m-%d}", "",
             "| Gate | Result | Pass |", "|---|---|---|"]
    lines += [f"| {g} | {v} | {'PASS' if ok else 'FAIL'} |" for g, v, ok in rows]
    lines += ["", f"**Overall: {'ALL GATES PASS' if all(ok for *_, ok in rows) else 'NOT READY FOR REAL MONEY'}**", "",
              f"Full period: {d['cagr']:.1%}/yr, volatility {d['vol']:.1%}, Sharpe {d['sharpe']:.2f}, max drawdown "
              f"{d['max_dd']:.1%}, {d['trips']} trades, fees ${d['fees']:,.0f}, funding ${d['funding']:,.0f}.",
              f"Unseen test windows stitched: Sharpe {annual_sharpe(oos):.2f}, max drawdown {max_drawdown(oos):.1%}, "
              f"total {(1 + oos).prod() - 1:+.1%}.", "", "## Return by calendar year", "", "| Year | Return |", "|---|---|"]
    lines += [f"| {y} | {v:+.1%} |" for y, v in years.items()]
    lines += ["", "## Unseen test windows", "", "| Test | Sharpe | Return |", "|---|---|---|"]
    lines += [f"| {w['test']} | {w['test_sharpe']:.2f} | {w['test_return']:+.1%} |" for w in wf.windows]
    lines += ["", "## Variants", "", "| Variant | Sharpe | CAGR | Max DD | Vol | Trades | Kill switch |",
              "|---|---|---|---|---|---|---|"]
    lines += [f"| {k} | {v['sharpe']:.2f} | {v['cagr']:.1%} | {v['max_dd']:.1%} | {v['vol']:.1%} | {v['trips']} | "
              f"{'FIRED' if v['killed'] else '-'} |" for k, v in results.items()]
    lines += ["", "## Risk events (default)", ""] + [f"- {t:%Y-%m-%d}: {e}" for t, e in events[-25:]]
    text = "\n".join(lines)
    print("\n" + text)
    out = Path("reports") / f"validation-core-big-{datetime.now(timezone.utc):%Y-%m-%d}.md"
    out.write_text(text, encoding="utf-8")
    _log(results)
    print(f"\nSaved {out}; logged {len(results)} variants")
    return 0


if __name__ == "__main__":
    sys.exit(main())
