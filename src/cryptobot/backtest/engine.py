"""Event-driven backtester for USDⓈ-M perpetuals.

One loop step per bar (daily by default; `BacktestConfig.bar` = 4h for the satellite).
Below "day" means "bar". Order of events inside a bar:

  1. Funding settled at the bar open (00:00 UTC) is charged on the position held
     overnight - i.e. BEFORE today's order fills.
  2. Orders decided at the previous close fill at today's OPEN +/- slippage,
     paying the taker fee. (Signal at close t-1 -> fill at open t: no look-ahead.)
  3. Stops are checked against today's range (mark-price bar if available, since
     live stops trigger on MARK_PRICE). Fill rules, see `stop_fill`:
       - bar opens beyond the stop (gap):
           "spec_strict"  -> fill at the bar extreme (low for a long) - pessimistic,
                             as strategy-risk-spec.md requires
           "open_if_gap"  -> fill at the open
       - otherwise: fill at the stop price minus slippage
  4. Funding settled later in the day (08:00, 16:00, or 4h/1h intervals) is charged
     on the position after fills/stops. Amount = position x mark price x rate;
     longs pay when the rate is positive, shorts receive.
  5. Equity is marked at today's close; the strategy sees bars up to and
     including today and returns tomorrow's target.

Simplifications (documented on purpose):
- Daily bars can't show the intraday path, so a stop is assumed to trigger
  before intraday funding settlements.
- Funding at intraday times uses the mark-price close of that day as the price
  (the rate is ~0.01%, so the price error is tiny).
- All orders are taker (market) orders. Maker entries come with the live engine.

Prices here are floats: fine for simulation. Real orders use Decimal (exchange/filters.py).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd


@dataclass(frozen=True)
class Target:
    """What the strategy wants to hold from the next open.

    qty: signed position size in coins (+ long, - short, 0 flat).
    stop: exchange stop price for that position (None = no stop).
    """
    qty: float
    stop: float | None = None


# Strategy signature: (symbol, bars up to and including today, current state) -> Target or None (keep as is)
Strategy = Callable[[str, pd.DataFrame, "PortfolioState"], "Target | None"]


@dataclass
class BacktestConfig:
    initial_equity: float = 20_000.0
    taker_fee: float = 0.0005                 # 0.05% of notional
    maker_fee: float = 0.0002                 # not used yet (all fills are taker)
    slippage_bps: dict[str, float] = field(default_factory=lambda: {"BTCUSDT": 3, "ETHUSDT": 3, "default": 8})
    stop_fill: str = "spec_strict"            # or "open_if_gap"
    use_funding: bool = True
    # Live exchange rules (verified 2026-10-28 on fapi.binance.com) so results reflect real lot sizes.
    qty_step: dict[str, float] = field(default_factory=lambda: {
        "BTCUSDT": 0.001, "ETHUSDT": 0.001, "SOLUSDT": 0.01, "BNBUSDT": 0.01, "XRPUSDT": 0.1, "DOGEUSDT": 1})
    min_notional: dict[str, float] = field(default_factory=lambda: {
        "BTCUSDT": 50, "ETHUSDT": 20, "SOLUSDT": 5, "BNBUSDT": 5, "XRPUSDT": 5, "DOGEUSDT": 5})
    # Liquidation: if equity at the day's worst mark price falls to maintenance margin
    # (|notional| x rate; BTCUSDT first tier ~0.4%), everything is closed at that price
    # and trading stops - like the account being liquidated on Binance.
    maint_margin_rate: float = 0.004
    bar: pd.Timedelta = pd.Timedelta(days=1)  # bar length of the data handed in
    # A coin whose data ends was delisted: a position still open is closed at its last
    # close, this much worse (delistings are announced, but the price usually sinks first).
    delist_penalty: float = 0.02

    def slip(self, symbol: str) -> float:
        return self.slippage_bps.get(symbol, self.slippage_bps.get("default", 0)) / 10_000


@dataclass
class Position:
    qty: float = 0.0
    entry_price: float = 0.0       # average entry
    stop: float | None = None
    trade_pnl: float = 0.0         # realized PnL - fees + funding since this position was opened


@dataclass
class PortfolioState:
    """Read-only view handed to the strategy each day."""
    equity: float
    positions: dict[str, Position]
    date: pd.Timestamp
    closed_trades: list[tuple[str, float]] = field(default_factory=list)   # (symbol, pnl) closed today
    stopped_out: set[str] = field(default_factory=set)                     # symbols whose stop hit today


@dataclass
class BacktestResult:
    equity: pd.Series                 # daily equity at close
    fills: pd.DataFrame               # every simulated fill
    funding: pd.DataFrame             # every funding payment
    totals: dict[str, float]          # fees, slippage cost, funding, realized PnL

    def metrics(self) -> dict[str, float]:
        return performance_metrics(self.equity)


def performance_metrics(equity: pd.Series) -> dict[str, float]:
    """CAGR, annualised vol, Sharpe (rf=0, 365 days - crypto trades every day), max drawdown.
    Intraday equity curves are first reduced to one value per day (the last one), so
    daily and 4h sleeves are measured the same way."""
    equity = daily_equity(equity)
    rets = equity.pct_change().dropna()
    years = max((equity.index[-1] - equity.index[0]).days / 365.0, 1e-9)
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1 if equity.iloc[-1] > 0 else -1.0
    vol = rets.std() * math.sqrt(365) if len(rets) > 1 else 0.0
    sharpe = (rets.mean() * 365) / vol if vol > 0 else 0.0
    max_dd = (equity / equity.cummax() - 1).min()
    return {"cagr": cagr, "vol": vol, "sharpe": sharpe, "max_drawdown": max_dd,
            "final_equity": equity.iloc[-1], "days": len(equity)}


def daily_equity(equity: pd.Series) -> pd.Series:
    """Equity at each day's last bar, labelled by that day (00:00 UTC). Daily curves pass through."""
    if len(equity) < 2 or (equity.index[1] - equity.index[0]) >= pd.Timedelta(days=1):
        return equity
    return equity.groupby(equity.index.floor("D")).last()


def round_qty(qty: float, step: float | None) -> float:
    """Round toward zero to the exchange step (never trade more than intended)."""
    if not step:
        return qty
    units = math.floor(abs(qty) / step + 1e-9)
    return math.copysign(round(units * step, 12), qty) if units else 0.0


class Backtester:
    def __init__(self, config: BacktestConfig, bars: dict[str, pd.DataFrame],
                 funding: dict[str, pd.DataFrame] | None = None,
                 mark_bars: dict[str, pd.DataFrame] | None = None):
        self.cfg = config
        self.bars = bars
        self.funding = funding or {}
        self.mark = mark_bars or {}

    def run(self, strategy: Strategy, start: str | None = None, end: str | None = None) -> BacktestResult:
        cfg = self.cfg
        dates = sorted(set().union(*(df.index for df in self.bars.values())))
        if start:
            dates = [d for d in dates if d >= pd.Timestamp(start, tz="UTC")]
        if end:
            dates = [d for d in dates if d <= pd.Timestamp(end, tz="UTC")]

        cash = cfg.initial_equity                    # realized PnL - fees + funding accumulate here
        positions = {s: Position() for s in self.bars}
        pending: dict[str, Target] = {}
        fills, payments, equity_curve = [], [], {}
        totals = {"fees": 0.0, "slippage_cost": 0.0, "funding": 0.0, "realized_pnl": 0.0}
        liquidated = False

        def fill(symbol, date, side_qty, price, reason, ref_price):
            """Apply a fill of `side_qty` (signed) at `price`; returns nothing, updates state."""
            nonlocal cash
            pos = positions[symbol]
            fee = abs(side_qty) * price * cfg.taker_fee
            slip_cost = abs(side_qty) * abs(price - ref_price)
            realized = 0.0
            if pos.qty != 0 and math.copysign(1, side_qty) != math.copysign(1, pos.qty):
                closed = min(abs(side_qty), abs(pos.qty))
                realized = closed * (price - pos.entry_price) * math.copysign(1, pos.qty)
            new_qty = round(pos.qty + side_qty, 12)
            if new_qty == 0:
                pos.entry_price = 0.0
            elif pos.qty == 0 or math.copysign(1, new_qty) != math.copysign(1, pos.qty):
                pos.entry_price = price                      # new position or flipped
            elif abs(new_qty) > abs(pos.qty):                # added to the position: average in
                pos.entry_price = (pos.entry_price * abs(pos.qty) + price * abs(side_qty)) / abs(new_qty)
            pos.trade_pnl += realized - fee
            if new_qty == 0:
                closed_today.append((symbol, pos.trade_pnl))
                pos.trade_pnl = 0.0
            if reason.startswith("stop"):
                stopped_today.add(symbol)
            pos.qty = new_qty
            cash += realized - fee
            totals["fees"] += fee
            totals["slippage_cost"] += slip_cost
            totals["realized_pnl"] += realized
            fills.append({"date": date, "symbol": symbol, "qty": side_qty, "price": price, "fee": fee,
                          "realized_pnl": realized, "reason": reason})

        def charge_funding(symbol, when, rate, price):
            nonlocal cash
            pos = positions[symbol]
            if pos.qty == 0 or not cfg.use_funding:
                return
            amount = -pos.qty * price * rate          # long + positive rate -> pays (negative amount)
            cash += amount
            pos.trade_pnl += amount
            totals["funding"] += amount
            payments.append({"time": when, "symbol": symbol, "qty": pos.qty, "price": price,
                             "rate": rate, "amount": amount})

        for date in dates:
            day_end = date + cfg.bar
            closed_today: list[tuple[str, float]] = []
            stopped_today: set[str] = set()
            for symbol, df in self.bars.items():
                if date not in df.index:
                    pos = positions[symbol]
                    if pos.qty != 0 and date > df.index[-1]:
                        last = df["close"].iloc[-1]
                        worse = 1 - cfg.delist_penalty if pos.qty > 0 else 1 + cfg.delist_penalty
                        fill(symbol, date, -pos.qty, last * worse, "delisted", last)
                        pos.stop = None
                        pending.pop(symbol, None)
                    continue
                bar = df.loc[date]
                mark = self.mark.get(symbol)
                mbar = mark.loc[date] if mark is not None and date in mark.index else bar
                pos = positions[symbol]
                fund = self.funding.get(symbol)
                at_open, later = [], []
                if fund is not None:
                    todays = fund[(fund.index >= date) & (fund.index < day_end)]
                    # Settlements in the first minute belong to the bar open (e.g. ...800005 ms).
                    cutoff = date + pd.Timedelta(minutes=1)
                    at_open = list(todays.loc[todays.index < cutoff, "rate"].items())
                    later = list(todays.loc[todays.index >= cutoff, "rate"].items())

                # 1. funding at the open, on the overnight position
                for when, rate in at_open:
                    charge_funding(symbol, when, rate, mbar["open"])

                # 2. fill yesterday's decision at today's open
                target = pending.pop(symbol, None)
                if target is not None:
                    want = round_qty(target.qty, cfg.qty_step.get(symbol))
                    delta = round(want - pos.qty, 12)
                    notional = abs(delta) * bar["open"]
                    reducing_to_zero = want == 0
                    if delta != 0 and (reducing_to_zero or notional >= cfg.min_notional.get(symbol, 0)):
                        slip = cfg.slip(symbol)
                        price = bar["open"] * (1 + slip if delta > 0 else 1 - slip)
                        if pos.qty != 0 and want != 0 and math.copysign(1, want) != math.copysign(1, pos.qty):
                            # A flip (long -> short) is a close and a new open: two trades, two records.
                            fill(symbol, date, -pos.qty, price, "signal", bar["open"])
                            fill(symbol, date, want, price, "signal", bar["open"])
                        else:
                            fill(symbol, date, delta, price, "signal", bar["open"])
                    if pos.qty != 0:
                        pos.stop = target.stop
                    else:
                        pos.stop = None

                # 3. stops (triggered by the mark-price range)
                if pos.qty != 0 and pos.stop is not None:
                    slip = cfg.slip(symbol)
                    if pos.qty > 0 and mbar["low"] <= pos.stop:
                        gapped = mbar["open"] <= pos.stop
                        if gapped:
                            price = bar["low"] if cfg.stop_fill == "spec_strict" else bar["open"] * (1 - slip)
                        else:
                            price = pos.stop * (1 - slip)
                        fill(symbol, date, -pos.qty, price, "stop_gap" if gapped else "stop", pos.stop)
                        pos.stop = None
                    elif pos.qty < 0 and mbar["high"] >= pos.stop:
                        gapped = mbar["open"] >= pos.stop
                        if gapped:
                            price = bar["high"] if cfg.stop_fill == "spec_strict" else bar["open"] * (1 + slip)
                        else:
                            price = pos.stop * (1 + slip)
                        fill(symbol, date, -pos.qty, price, "stop_gap" if gapped else "stop", pos.stop)
                        pos.stop = None

                # 4. funding later in the day, on the position after fills/stops
                for when, rate in later:
                    charge_funding(symbol, when, rate, mbar["close"])

            # 4b. liquidation check at the day's worst mark price for every open position
            if not liquidated and self._liquidation_hit(date, positions, cash):
                for s, p in positions.items():
                    if p.qty != 0:
                        mb = self._mark_bar(s, date)
                        fill(s, date, -p.qty, mb["low"] if p.qty > 0 else mb["high"], "liquidation",
                             mb["low"] if p.qty > 0 else mb["high"])
                        p.stop = None
                liquidated = True
                pending.clear()
                # Binance's insurance fund absorbs losses beyond the balance: it can't go below 0.
                cash = max(cash, 0.0)
                totals["liquidation_date"] = date

            # 5. mark to market at the close, then ask the strategy for tomorrow
            unrealized = sum(p.qty * (self.bars[s].loc[date, "close"] - p.entry_price)
                             for s, p in positions.items() if p.qty != 0 and date in self.bars[s].index)
            equity = cash + unrealized
            equity_curve[date] = equity
            if liquidated:
                continue  # account blown: no new orders
            state = PortfolioState(equity=equity, positions=positions, date=date,
                                   closed_trades=closed_today, stopped_out=stopped_today)
            # Only bars up to today's close are handed over - never anything later.
            histories = {s: df.loc[:date] for s, df in self.bars.items() if date in df.index}
            if hasattr(strategy, "targets"):
                # Portfolio strategy: decides all symbols together (needed for portfolio caps).
                for symbol, target in strategy.targets(histories, state).items():
                    if target is not None:
                        pending[symbol] = target
            else:
                for symbol, history in histories.items():
                    target = strategy(symbol, history, state)
                    if target is not None:
                        pending[symbol] = target

        totals["liquidated"] = liquidated
        return BacktestResult(
            equity=pd.Series(equity_curve, name="equity"),
            fills=pd.DataFrame(fills),
            funding=pd.DataFrame(payments),
            totals=totals,
        )

    def _mark_bar(self, symbol: str, date: pd.Timestamp) -> pd.Series:
        mark = self.mark.get(symbol)
        return mark.loc[date] if mark is not None and date in mark.index else self.bars[symbol].loc[date]

    def _liquidation_hit(self, date: pd.Timestamp, positions: dict[str, Position], cash: float) -> bool:
        """Account-level check (conservative simplification of per-position isolated margin)."""
        open_ = {s: p for s, p in positions.items() if p.qty != 0 and date in self.bars[s].index}
        if not open_:
            return False
        worst_equity, maintenance = cash, 0.0
        for s, p in open_.items():
            mb = self._mark_bar(s, date)
            worst = mb["low"] if p.qty > 0 else mb["high"]
            worst_equity += p.qty * (worst - p.entry_price)
            maintenance += abs(p.qty) * worst * self.cfg.maint_margin_rate
        return worst_equity <= maintenance


def buy_and_hold(fraction: float = 1.0) -> Strategy:
    """Sanity-check strategy: go long `fraction` x equity (split over the symbols) and hold."""
    def strategy(symbol: str, history: pd.DataFrame, state: PortfolioState) -> Target | None:
        if state.positions[symbol].qty != 0:
            return None  # already holding: keep it
        price = history["close"].iloc[-1]
        return Target(qty=state.equity * fraction / len(state.positions) / price)
    return strategy
