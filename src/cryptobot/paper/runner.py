"""Level 1 paper trading: the two sleeves on real prices, with no orders at all.

Every day both sleeves are replayed from the fixed start date through the same
backtester used for validation. Replaying instead of keeping live state means there is
no separate code path that could behave differently from the tests, and circuit
breakers rebuild themselves from the record. No exchange order code is imported here.

New would-be fills are appended to the normal `fills` table with environment
'paper-core' / 'paper-xs', so the trades and stats commands show them separately.
A fill that was recorded earlier must come out the same on every replay; if it doesn't
(Binance revised a candle, or someone changed a setting), the owner is warned.

Paper capital and the start date live in config/paper.yaml.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yaml

from cryptobot.backtest.data import MarketDataStore
from cryptobot.backtest.engine import Backtester, BacktestConfig, BacktestResult
from cryptobot.backtest.loading import load_history
from cryptobot.backtest.universe import Universe
from cryptobot.levels import require_level
from cryptobot.strategies.donchian_portfolio import DonchianPortfolio, load_overlay_config, load_strategy_config
from cryptobot.strategies.xs_momentum import XSMomentum, load_xs_config, panels

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_equity (
    sleeve TEXT NOT NULL, day TEXT NOT NULL, equity REAL NOT NULL, PRIMARY KEY (sleeve, day)
);
"""


@dataclass
class PaperResult:
    equity: dict[str, float] = field(default_factory=dict)       # sleeve -> today's equity
    start_equity: dict[str, float] = field(default_factory=dict)
    new_fills: int = 0
    problems: list[str] = field(default_factory=list)
    open_positions: dict[str, list[str]] = field(default_factory=dict)
    killed: list[str] = field(default_factory=list)

    def text(self) -> str:
        total, base = sum(self.equity.values()), sum(self.start_equity.values())
        lines = [f"Paper trading: ${total:,.0f} ({total / base - 1:+.1%} since start), {self.new_fills} new fills"]
        for s, eq in self.equity.items():
            held = ", ".join(self.open_positions.get(s, [])) or "flat"
            lines.append(f"  {s}: ${eq:,.0f} ({eq / self.start_equity[s] - 1:+.1%}) - {held}")
        lines += [f"  KILL SWITCH fired in {s}: needs your review" for s in self.killed]
        lines += [f"  WARNING: {p}" for p in self.problems]
        return "\n".join(lines)


def sync_fills(db: str, sleeve: str, fills: pd.DataFrame) -> tuple[int, list[str]]:
    """Append fills not recorded yet; report any recorded fill that a replay changed."""
    env = f"paper-{sleeve}"
    con = sqlite3.connect(db)
    try:
        known = {r[0]: r[1:] for r in con.execute(
            "SELECT order_id, quantity, price FROM fills WHERE environment=?", (env,))}
        added, problems = 0, []
        seen: dict[str, int] = {}
        for _, f in fills.iterrows():
            base = f"{env}-{f['date']:%Y-%m-%d}-{f['symbol']}-{f['reason']}"
            seen[base] = seen.get(base, 0) + 1
            order_id = f"{base}-{seen[base]}"
            qty, price = abs(float(f["qty"])), float(f["price"])
            if order_id in known:
                old_qty, old_price = known[order_id]
                if abs(old_qty - qty) > 1e-9 * max(qty, 1) or abs(old_price - price) > 1e-9 * max(price, 1):
                    problems.append(f"{order_id} changed on replay: {old_qty} @ {old_price} -> {qty} @ {price}")
                continue
            con.execute("INSERT INTO fills (order_id, timestamp, environment, symbol, side, quantity, price, "
                        "quote_value, fee, fee_asset, realized_pnl, client_order_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (order_id, pd.Timestamp(f["date"]).isoformat(), env, f["symbol"],
                         "buy" if f["qty"] > 0 else "sell", qty, price, qty * price, float(f["fee"]), "USDT",
                         float(f["realized_pnl"]), order_id))
            added += 1
        con.commit()
        return added, problems
    finally:
        con.close()


def save_equity(db: str, sleeve: str, equity: pd.Series) -> None:
    con = sqlite3.connect(db)
    try:
        con.executescript(SCHEMA)
        con.executemany("INSERT OR REPLACE INTO paper_equity VALUES (?,?,?)",
                        [(sleeve, f"{d:%Y-%m-%d}", float(v)) for d, v in equity.items()])
        con.commit()
    finally:
        con.close()


def _core(start: str, capital: float, config: str, market_db: str) -> tuple[BacktestResult, DonchianPortfolio]:
    strat, risk, raw = load_strategy_config(config)
    symbols = raw["symbols"]["core"]
    store = MarketDataStore(market_db)
    store.update(symbols)
    store.close()
    hist = load_history(symbols, db=market_db, extended=True)
    bt = BacktestConfig(initial_equity=capital * raw["capital_share"], taker_fee=raw["costs"]["taker"],
                        slippage_bps=dict(raw["costs"]["slippage_bps"]))
    strategy = DonchianPortfolio(hist.bars, strat, risk, funding=hist.funding, overlay_cfg=load_overlay_config(raw),
                                 btc_bars=hist.btc)
    return Backtester(bt, hist.bars, hist.funding, hist.marks).run(strategy, start=start), strategy


def _xs(start: str, capital: float, config: str, universe_db: str) -> tuple[BacktestResult, XSMomentum]:
    cfg, risk, raw = load_xs_config(config)
    u = Universe(universe_db)
    u.refresh_symbols()
    u.top_up()
    names = [r[0] for r in u.db.execute("SELECT symbol FROM usymbols WHERE underlying_type IN ('COIN', 'UNKNOWN')")]
    all_bars = {s: u.bars(s) for s in names}
    all_bars = {s: d for s, d in all_bars.items() if len(d) > cfg.min_age_days}
    p = panels(all_bars, None, cfg)
    ok = p["liquidity"].where(p["age"] >= cfg.min_age_days)
    ever = sorted(s for s in ok.columns if (ok.rank(axis=1, ascending=False)[s] <= cfg.universe_size).any())
    u.top_up_funding(ever)
    bars = {s: all_bars[s] for s in ever}
    funding = {s: u.funding(s) for s in ever}
    bt = BacktestConfig(initial_equity=capital * raw["capital_share"], taker_fee=raw["costs"]["taker"],
                        slippage_bps=dict(raw["costs"]["slippage_bps"]))
    strategy = XSMomentum(bars, cfg, risk, funding=funding)
    return Backtester(bt, bars, funding).run(strategy, start=start), strategy


def run_paper(level: int, trades_db: str, market_db: str = "data/market.sqlite",
              universe_db: str = "data/universe.sqlite", settings: str | Path = "config/paper.yaml") -> PaperResult:
    require_level(level, "paper_trade")
    with open(settings, encoding="utf-8") as f:
        s = yaml.safe_load(f)
    start, capital = str(s["start_date"]), float(s["capital"])
    out = PaperResult()
    sleeves = {"core": _core(start, capital, s["core_config"], market_db),
               "xs": _xs(start, capital, s["xs_config"], universe_db)}
    for name, (res, strategy) in sleeves.items():
        out.start_equity[name] = float(res.equity.iloc[0])
        out.equity[name] = float(res.equity.iloc[-1])
        save_equity(trades_db, name, res.equity)
        if not res.fills.empty:
            added, problems = sync_fills(trades_db, name, res.fills)
            out.new_fills += added
            out.problems += problems
            held = res.fills.groupby("symbol")["qty"].sum().round(9)
            out.open_positions[name] = [f"{'long' if q > 0 else 'short'} {sym}" for sym, q in held.items() if q != 0]
        if strategy.breakers.stopped:
            out.killed.append(name)
    return out
