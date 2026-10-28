"""Run a backtest on the stored history (data/market.sqlite).

    python scripts/run_backtest.py                      # buy & hold sanity check, BTCUSDT + ETHUSDT

Step 2 of strategy-risk-spec.md: only the buy-and-hold sanity strategy exists yet.
It checks the engine against something we can verify by hand: the result must be
close to the price change, minus fees, minus funding paid by a long.
The Donchian ensemble (step 3) will plug into the same engine.
"""

from __future__ import annotations

import argparse
import sys

from cryptobot.backtest.data import MarketDataStore
from cryptobot.backtest.engine import Backtester, BacktestConfig, buy_and_hold


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("symbols", nargs="*", default=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--equity", type=float, default=20_000)
    parser.add_argument("--db", default="data/market.sqlite")
    args = parser.parse_args()

    store = MarketDataStore(args.db)
    bars = {s: store.bars(s, "trade") for s in args.symbols}
    marks = {s: store.bars(s, "mark") for s in args.symbols}
    funding = {s: store.funding(s) for s in args.symbols}
    store.close()
    for s in args.symbols:
        if bars[s].empty:
            print(f"No data for {s} - run scripts/download_history.py first", file=sys.stderr)
            return 1

    print(f"Buy & hold, {', '.join(args.symbols)} (equal split), start equity {args.equity:,.0f} USDT")
    for label, use_funding in (("with funding", True), ("without funding", False)):
        cfg = BacktestConfig(initial_equity=args.equity, use_funding=use_funding)
        res = Backtester(cfg, bars, funding, marks).run(buy_and_hold(), start=args.start, end=args.end)
        m, t = res.metrics(), res.totals
        print(f"\n--- {label} ---")
        print(f"period {res.equity.index[0]:%Y-%m-%d} -> {res.equity.index[-1]:%Y-%m-%d} ({m['days']} days)")
        print(f"final equity {m['final_equity']:,.0f}  CAGR {m['cagr']:.1%}  vol {m['vol']:.1%}  "
              f"Sharpe {m['sharpe']:.2f}  max drawdown {m['max_drawdown']:.1%}")
        print(f"fees {t['fees']:,.2f}  slippage {t['slippage_cost']:,.2f}  funding {t['funding']:,.2f}  "
              f"({len(res.funding)} settlements)")
        if t["liquidated"]:
            print(f"LIQUIDATED on {t['liquidation_date']:%Y-%m-%d} - funding drained the wallet while the "
                  f"position was never resized")

    # Hand check: price change of each half, ignoring costs.
    print("\n--- hand check (price only, no costs) ---")
    for s in args.symbols:
        df = bars[s].loc[args.start:args.end] if args.end else bars[s].loc[args.start:]
        first_fill, last = df["open"].iloc[1], df["close"].iloc[-1]
        print(f"{s}: open {first_fill:,.2f} (day 2) -> close {last:,.2f}  = x{last / first_fill:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
