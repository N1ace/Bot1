"""Run backtests on the stored history (data/market.sqlite).

    python scripts/run_backtest.py                         # Donchian ensemble, settings from config/strategy.yaml
    python scripts/run_backtest.py --r 0.0025              # risk per trade 0.25% (live ramp start)
    python scripts/run_backtest.py --shorts                # also allow shorts (0.5x weight)
    python scripts/run_backtest.py --strategy buyhold      # buy & hold sanity check

Uses the live lot sizes (0.001 BTC / ETH) and minimum order values, taker fees,
slippage, real funding, mark-price stops with spec-strict gap fills.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys

import pandas as pd

from cryptobot.backtest.data import MarketDataStore
from cryptobot.backtest.engine import Backtester, BacktestConfig, buy_and_hold
from cryptobot.strategies.donchian_portfolio import DonchianPortfolio, load_overlay_config, load_strategy_config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", choices=["donchian", "buyhold"], default="donchian")
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--equity", type=float, default=20_000)
    parser.add_argument("--r", type=float, default=None, help="risk per trade, overrides config")
    parser.add_argument("--shorts", action="store_true")
    parser.add_argument("--stop-fill", default="spec_strict", choices=["spec_strict", "open_if_gap"])
    parser.add_argument("--no-funding", action="store_true")
    parser.add_argument("--no-overlay", action="store_true", help="switch off regime overlay + funding layer")
    parser.add_argument("--config", default="config/strategy.yaml")
    parser.add_argument("--db", default="data/market.sqlite")
    args = parser.parse_args()

    strat_cfg, risk_cfg, raw = load_strategy_config(args.config)
    if args.r is not None:
        risk_cfg = dataclasses.replace(risk_cfg, risk_per_trade=args.r)
    if args.shorts:
        strat_cfg = dataclasses.replace(strat_cfg, allow_shorts=True)
    symbols = args.symbols or raw["symbols"]["core"]

    store = MarketDataStore(args.db)
    bars = {s: store.bars(s, "trade") for s in symbols}
    marks = {s: store.bars(s, "mark") for s in symbols}
    funding = {s: store.funding(s) for s in symbols}
    store.close()
    if any(df.empty for df in bars.values()):
        print("Missing data - run scripts/download_history.py first", file=sys.stderr)
        return 1

    costs = raw["costs"]
    bt_cfg = BacktestConfig(initial_equity=args.equity, taker_fee=costs["taker"], maker_fee=costs["maker"],
                            slippage_bps=dict(costs["slippage_bps"]), stop_fill=args.stop_fill,
                            use_funding=not args.no_funding)
    if args.strategy == "buyhold":
        strategy, title = buy_and_hold(), "Buy & hold"
    else:
        overlay_cfg = load_overlay_config(raw)
        if args.no_overlay:
            overlay_cfg = dataclasses.replace(overlay_cfg, enabled=False)
        btc = bars.get("BTCUSDT")
        if btc is None:
            store = MarketDataStore(args.db)
            btc = store.bars("BTCUSDT", "trade")
            store.close()
        strategy = DonchianPortfolio(bars, strat_cfg, risk_cfg, funding=funding, overlay_cfg=overlay_cfg, btc_bars=btc)
        title = (f"Donchian ensemble {list(strat_cfg.lookbacks)}, risk/trade {risk_cfg.risk_per_trade:.2%}, "
                 f"{'long+short' if strat_cfg.allow_shorts else 'long only'}, "
                 f"overlay {'on' if overlay_cfg.enabled else 'off'}")
    res = Backtester(bt_cfg, bars, funding, marks).run(strategy, start=args.start, end=args.end)

    for w in getattr(strategy, "overlay_warnings", []):
        print(f"WARNING: {w}")
    m, t = res.metrics(), res.totals
    fills = res.fills
    print(f"{title}\n{', '.join(symbols)} | start {args.equity:,.0f} USDT | stops: {args.stop_fill} | "
          f"funding {'off' if args.no_funding else 'on'}")
    print(f"period {res.equity.index[0]:%Y-%m-%d} -> {res.equity.index[-1]:%Y-%m-%d}")
    print(f"\nfinal equity {m['final_equity']:,.0f}   CAGR {m['cagr']:.1%}   Sharpe {m['sharpe']:.2f}   "
          f"max drawdown {m['max_drawdown']:.1%}   volatility {m['vol']:.1%}")
    print(f"costs: fees {t['fees']:,.0f}  slippage {t['slippage_cost']:,.0f}  funding {t['funding']:,.0f}")
    if t.get("liquidated"):
        print(f"LIQUIDATED on {t['liquidation_date']:%Y-%m-%d}")

    if not fills.empty:
        by_reason = fills["reason"].value_counts().to_dict()
        print(f"fills: {len(fills)} {by_reason}")
    if isinstance(strategy, DonchianPortfolio):
        closed = _round_trips(fills)
        if closed:
            wins = sum(1 for p in closed if p > 0)
            print(f"round trips (position opened -> fully closed): {len(closed)}, won {wins} "
                  f"({wins / len(closed):.0%}), average {sum(closed) / len(closed):,.0f} USDT")
        exposure, avg_notional = _exposure(fills, bars, res.equity)
        print(f"days with a position: {exposure:.0%}   average position size while invested: "
              f"{avg_notional:.0%} of equity")
        if strategy.breakers.events:
            print(f"\nrisk events ({len(strategy.breakers.events)}):")
            for date, text in strategy.breakers.events[:15]:
                print(f"  {date:%Y-%m-%d}  {text}")
            if len(strategy.breakers.events) > 15:
                print(f"  ... {len(strategy.breakers.events) - 15} more")

    print("\nyear     return   max drawdown")
    for year, eq in res.equity.groupby(res.equity.index.year):
        start = res.equity[res.equity.index < eq.index[0]]
        base = start.iloc[-1] if len(start) else eq.iloc[0]
        dd = (eq / eq.cummax() - 1).min()
        print(f"{year}   {eq.iloc[-1] / base - 1:>7.1%}   {dd:>7.1%}")
    return 0


def _round_trips(fills: pd.DataFrame) -> list[float]:
    """PnL (realized - fees) of each position from open to flat, per symbol."""
    out = []
    for _, f in fills.groupby("symbol"):
        pos, pnl = 0.0, 0.0
        for _, row in f.iterrows():
            pnl += row["realized_pnl"] - row["fee"]
            pos = round(pos + row["qty"], 12)
            if pos == 0:
                out.append(pnl)
                pnl = 0.0
    return out


def _exposure(fills: pd.DataFrame, bars: dict[str, pd.DataFrame], equity: pd.Series) -> tuple[float, float]:
    """(share of days holding anything, average |notional| / equity on those days)."""
    if fills.empty:
        return 0.0, 0.0
    notional = pd.Series(0.0, index=equity.index)
    for symbol, f in fills.groupby("symbol"):
        qty = f.groupby("date")["qty"].sum().reindex(equity.index, fill_value=0.0).cumsum().round(12)
        notional += qty.abs() * bars[symbol]["close"].reindex(equity.index)
    invested = notional > 0
    share = invested.mean()
    avg = (notional[invested] / equity[invested]).mean() if invested.any() else 0.0
    return share, avg


if __name__ == "__main__":
    sys.exit(main())
