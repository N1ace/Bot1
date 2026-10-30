"""One pre-decided test of the crowded-trade filter on the bigger-bets core.

    python scripts/download_metrics.py      # first
    python scripts/validate_oi_filter.py

Rule (fixed before the test): open interest grew more than 2 standard deviations faster
than normal over 7 days AND funding shows the crowd is on our side -> half size.
Compared with the same core without the filter, from the first day all 6 coins have
open-interest history. Thresholds 1.6 and 2.4 are the +-20% stability check.
"""

from __future__ import annotations

import dataclasses
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from validate_sleeves import INITIAL, _log, _summarise

from cryptobot.backtest.engine import Backtester, BacktestConfig
from cryptobot.backtest.loading import load_history
from cryptobot.strategies.donchian_portfolio import DonchianPortfolio, load_overlay_config, load_strategy_config


def open_interest(symbols: list[str]) -> tuple[dict[str, pd.Series], str]:
    with sqlite3.connect("data/market.sqlite") as con:
        df = pd.read_sql_query("SELECT symbol, day, open_interest_value FROM metrics_daily", con)
    df["day"] = pd.to_datetime(df["day"], utc=True)
    out = {s: g.set_index("day")["open_interest_value"].sort_index() for s, g in df.groupby("symbol") if s in symbols}
    missing = set(symbols) - set(out)
    if missing:
        raise RuntimeError(f"No open interest for {missing} - run scripts/download_metrics.py")
    return out, max(v.index.min() for v in out.values()).strftime("%Y-%m-%d")


def main() -> int:
    strat, risk, raw = load_strategy_config("config/core_big.yaml")
    base = load_overlay_config(raw)
    symbols = raw["symbols"]["core"]
    hist = load_history(symbols, extended=True)
    oi, start = open_interest(symbols)
    bt = BacktestConfig(initial_equity=INITIAL * raw["capital_share"], taker_fee=raw["costs"]["taker"],
                        slippage_bps=dict(raw["costs"]["slippage_bps"]))
    variants = {
        "oi: no filter": base,
        "oi: filter z > 2.0": dataclasses.replace(base, oi_crowd_z=2.0),
        "+-20%: oi filter z > 1.6": dataclasses.replace(base, oi_crowd_z=1.6),
        "+-20%: oi filter z > 2.4": dataclasses.replace(base, oi_crowd_z=2.4),
    }
    results, rets = {}, {}
    for name, ov in variants.items():
        strategy = DonchianPortfolio(hist.bars, strat, risk, funding=hist.funding, overlay_cfg=ov, btc_bars=hist.btc,
                                     open_interest=oi)
        res = Backtester(bt, hist.bars, hist.funding, hist.marks).run(strategy, start=start)
        results[name], rets[name] = _summarise(name, res, strategy.breakers.stopped)

    text = "\n".join([f"# Crowded-trade filter, one test {datetime.now(timezone.utc):%Y-%m-%d}", "",
                      f"Period {start} to today, all 6 coins with open-interest history.", "",
                      "| Variant | Sharpe | CAGR | Max DD | Vol | Trades |", "|---|---|---|---|---|---|",
                      *[f"| {k} | {v['sharpe']:.2f} | {v['cagr']:.1%} | {v['max_dd']:.1%} | {v['vol']:.1%} | {v['trips']} |"
                        for k, v in results.items()]])
    print("\n" + text)
    Path("reports", f"validation-oi-{datetime.now(timezone.utc):%Y-%m-%d}.md").write_text(text, encoding="utf-8")
    _log(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
