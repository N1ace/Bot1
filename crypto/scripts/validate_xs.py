"""One pre-decided test of strongest-vs-weakest coins (config/xs.yaml), dead coins included.

    python scripts/download_universe.py     # first
    python scripts/validate_xs.py

Runs the default, its +-20% neighbours and a doubled-cost check; prints the section-7 gates
for this sleeve and for 60% bigger-bets core + 40% this sleeve. Every run is logged.
"""

from __future__ import annotations

import dataclasses
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from validate_sleeves import INITIAL, _log, _logged_sharpes, _summarise, gates

from cryptobot.backtest.engine import Backtester, BacktestConfig, daily_equity
from cryptobot.backtest.loading import load_history
from cryptobot.backtest.universe import Universe
from cryptobot.backtest.validation import annual_sharpe, deflated_sharpe_probability, max_drawdown, walk_forward
from cryptobot.strategies.donchian_portfolio import DonchianPortfolio, load_overlay_config, load_strategy_config
from cryptobot.strategies.xs_momentum import XSMomentum, load_xs_config, panels

DEFAULT = "sel: xs DEFAULT"


def load_coins(u: Universe, cfg) -> tuple[dict, dict, list[str]]:
    """Bars and funding of every coin that was ever in the weekly universe."""
    names = [r[0] for r in u.db.execute("SELECT symbol FROM usymbols WHERE underlying_type IN ('COIN', 'UNKNOWN')")]
    all_bars = {s: u.bars(s) for s in names}
    all_bars = {s: d for s, d in all_bars.items() if len(d) > cfg.min_age_days}
    p = panels(all_bars, None, cfg)
    ok = p["liquidity"].where(p["age"] >= cfg.min_age_days)
    ever = sorted(s for s in ok.columns if (ok.rank(axis=1, ascending=False)[s] <= cfg.universe_size).any())
    u.download_funding(ever)
    bars = {s: all_bars[s] for s in ever}
    funding = {s: u.funding(s) for s in ever}
    status = dict(u.db.execute("SELECT symbol, status FROM usymbols").fetchall())
    dead = [s for s in ever if status.get(s) != "TRADING"]
    notes = [f"{len(all_bars)} coins with 90+ days of history, {len(ever)} were ever in the top "
             f"{cfg.universe_size}, {len(dead)} of those no longer trade: {', '.join(dead[:30])}"]
    return bars, funding, notes


def core_returns() -> pd.Series:
    """The bigger-bets core again (same settings as its own test, so not a new variant)."""
    strat, risk, raw = load_strategy_config("config/core_big.yaml")
    hist = load_history(raw["symbols"]["core"], extended=True)
    bt = BacktestConfig(initial_equity=INITIAL * raw["capital_share"], taker_fee=raw["costs"]["taker"],
                        slippage_bps=dict(raw["costs"]["slippage_bps"]))
    strategy = DonchianPortfolio(hist.bars, strat, risk, funding=hist.funding, overlay_cfg=load_overlay_config(raw),
                                 btc_bars=hist.btc)
    return daily_equity(Backtester(bt, hist.bars, hist.funding, hist.marks).run(strategy).equity).pct_change().fillna(0)


def main() -> int:
    cfg, risk, raw = load_xs_config()
    u = Universe()
    bars, funding, notes = load_coins(u, cfg)
    print(notes[0], flush=True)
    costs = raw["costs"]

    def run(name, c, r, slip=None):
        bt = BacktestConfig(initial_equity=INITIAL * raw["capital_share"], taker_fee=costs["taker"],
                            slippage_bps=slip or dict(costs["slippage_bps"]))
        strategy = XSMomentum(bars, c, r, funding=funding)
        res = Backtester(bt, bars, funding).run(strategy)
        out, rets = _summarise(name, res, strategy.breakers.stopped)
        return out, rets, res, strategy

    wide = dataclasses.replace(risk, max_positions=10)      # 5 + 5 coins needs 10 slots, for this neighbour only
    variants = {
        DEFAULT: (cfg, risk),
        "+-20%: xs lookback 22 days": (dataclasses.replace(cfg, lookback_days=22), risk),
        "+-20%: xs lookback 34 days": (dataclasses.replace(cfg, lookback_days=34), risk),
        "+-20%: xs 3 + 3 coins": (dataclasses.replace(cfg, n_long=3, n_short=3), risk),
        "+-20%: xs 5 + 5 coins": (dataclasses.replace(cfg, n_long=5, n_short=5), wide),
        "+-20%: xs position vol 4%": (dataclasses.replace(cfg, position_vol=0.04), risk),
        "+-20%: xs position vol 6%": (dataclasses.replace(cfg, position_vol=0.06), risk),
        "+-20%: xs stop 2.4 ATR": (dataclasses.replace(cfg, k_atr=2.4), dataclasses.replace(risk, k_atr=2.4)),
        "+-20%: xs stop 3.6 ATR": (dataclasses.replace(cfg, k_atr=3.6), dataclasses.replace(risk, k_atr=3.6)),
    }
    results, rets, default_res, default_strategy = {}, {}, None, None
    for name, (c, r) in variants.items():
        results[name], rets[name], res, strategy = run(name, c, r)
        if name == DEFAULT:
            default_res, default_strategy = res, strategy
    double = {k: v * 2 for k, v in costs["slippage_bps"].items()}
    results["cost: xs slippage x2"], _, _, _ = run("cost: xs slippage x2", cfg, risk, double)

    trial_daily = [r["sharpe"] / 365 ** 0.5 for r in results.values()] + [s / 365 ** 0.5 for s in _logged_sharpes()]
    rows, wf, oos = gates("XS", results, rets, trial_daily, DEFAULT)

    core = core_returns()
    x = rets[DEFAULT]
    start = default_res.fills["date"].min()
    x_live = x[x.index >= start]
    common = core.index.intersection(x_live.index)
    corr = core[common].corr(x_live[common])
    weights = (0.6, 0.4)
    eq = weights[0] * (1 + core[common]).cumprod() + weights[1] * (1 + x_live[common]).cumprod()
    mix = eq.pct_change().fillna(eq.iloc[0] - 1)
    core_oos = walk_forward({"core": core}).oos_returns
    both = core_oos.index.intersection(oos.index)
    eq_oos = weights[0] * (1 + core_oos[both]).cumprod() + weights[1] * (1 + oos[both]).cumprod()
    mix_oos = eq_oos.pct_change().fillna(eq_oos.iloc[0] - 1)
    dsr = deflated_sharpe_probability(mix_oos, trial_daily)
    rows += [("Mix 3a Out-of-sample Sharpe > 0.8", f"{annual_sharpe(mix_oos):.2f}", annual_sharpe(mix_oos) > 0.8),
             ("Mix 3b Max drawdown < 35%", f"{max_drawdown(mix):.1%}", max_drawdown(mix) > -0.35),
             ("Mix 5 Deflated Sharpe > 0.95", f"{dsr:.2f}", dsr > 0.95)]

    f = default_res.fills
    closes = f[f["realized_pnl"] != 0]
    longs_pnl = closes.loc[closes["qty"] < 0, "realized_pnl"].sum()
    shorts_pnl = closes.loc[closes["qty"] > 0, "realized_pnl"].sum()
    eq_d = daily_equity(default_res.equity)
    by_year = eq_d.groupby(eq_d.index.year).last()
    years = by_year.pct_change()
    years.iloc[0] = by_year.iloc[0] / eq_d.iloc[0] - 1
    d = results[DEFAULT]

    lines = [f"# Strongest vs weakest coins, one test {datetime.now(timezone.utc):%Y-%m-%d}", "", *notes,
             f"First trade {start:%Y-%m-%d}.", "", "| Gate | Result | Pass |", "|---|---|---|"]
    lines += [f"| {g} | {v} | {'PASS' if ok else 'FAIL'} |" for g, v, ok in rows]
    lines += ["", f"**Overall: {'ALL GATES PASS' if all(ok for *_, ok in rows) else 'NOT READY FOR REAL MONEY'}**", "",
              f"Sleeve: {d['cagr']:.1%}/yr, volatility {d['vol']:.1%}, Sharpe {d['sharpe']:.2f}, max drawdown "
              f"{d['max_dd']:.1%}, {d['trips']} trades, fees ${d['fees']:,.0f}, funding ${d['funding']:,.0f}, "
              f"slippage ${default_res.totals['slippage_cost']:,.0f}.",
              f"Price profit from longs ${longs_pnl:,.0f}, from shorts ${shorts_pnl:,.0f}. "
              f"Delisted while held: {(f['reason'] == 'delisted').sum()}.",
              f"Unseen windows: Sharpe {annual_sharpe(oos):.2f}, max drawdown {max_drawdown(oos):.1%}, "
              f"total {(1 + oos).prod() - 1:+.1%}.",
              f"Mix 60/40 with the bigger-bets core: Sharpe {annual_sharpe(mix):.2f}, max drawdown "
              f"{max_drawdown(mix):.1%}, total {(1 + mix).prod() - 1:+.1%}; daily correlation {corr:.2f}.",
              "", "## Return by year", "", "| Year | Return |", "|---|---|"]
    lines += [f"| {y} | {v:+.1%} |" for y, v in years.items()]
    lines += ["", "## Unseen test windows", "", "| Test | Sharpe | Return |", "|---|---|---|"]
    lines += [f"| {w['test']} | {w['test_sharpe']:.2f} | {w['test_return']:+.1%} |" for w in wf.windows]
    lines += ["", "## Variants", "", "| Variant | Sharpe | CAGR | Max DD | Vol | Trades | Kill switch |",
              "|---|---|---|---|---|---|---|"]
    lines += [f"| {k} | {v['sharpe']:.2f} | {v['cagr']:.1%} | {v['max_dd']:.1%} | {v['vol']:.1%} | {v['trips']} | "
              f"{'FIRED' if v['killed'] else '-'} |" for k, v in results.items()]
    lines += ["", "## Risk events", ""] + [f"- {t:%Y-%m-%d}: {e}" for t, e in default_strategy.breakers.events[-20:]]
    text = "\n".join(lines)
    print("\n" + text)
    out = Path("reports") / f"validation-xs-{datetime.now(timezone.utc):%Y-%m-%d}.md"
    out.write_text(text, encoding="utf-8")
    _log(results)
    print(f"\nSaved {out}; logged {len(results)} variants")
    return 0


if __name__ == "__main__":
    sys.exit(main())
