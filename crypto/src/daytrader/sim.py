"""Fast intraday simulator: one symbol, 1-hour bars, one position at a time.

A tactic decides at a bar's close, using nothing later. Then, pessimistically on purpose:
  - entry at the NEXT bar's open, with slippage and the taker fee
  - stops trigger on the mark-price range; a gap past the stop fills at the bar's extreme
  - if stop and target are both inside one bar, the stop is assumed to come first
  - target fills pay the taker fee too (no maker discount is assumed)
  - any open position is closed at the last bar of the UTC day: this is a day trader
  - funding is charged at each settlement while a position is held
  - a coin swings a little between bars; nothing inside a bar is assumed to help us

Sizing: risk `risk_per_trade` of a fixed account on each trade, notional capped at 1x
per symbol. Returns are added, not compounded, so the early years can't dominate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

TAKER_FEE = 0.0005
SLIPPAGE = {"BTCUSDT": 0.0003, "ETHUSDT": 0.0003, "SOLUSDT": 0.0006}
RISK_PER_TRADE = 0.005
MAX_NOTIONAL = 1.0


@dataclass
class Result:
    trades: pd.DataFrame        # symbol, entry_time, exit_time, side, gross, net, r, reason
    daily: pd.Series            # return on the account per UTC day


def simulate(symbol: str, bars: pd.DataFrame, sig: pd.DataFrame, mark: pd.DataFrame | None = None,
             funding: pd.Series | None = None, cost_mult: float = 1.0) -> pd.DataFrame:
    """Trades for one symbol. `sig` has side (+1/-1/0), stop (distance), target (distance or NaN), max_bars."""
    n = len(bars)
    o, h, l, c = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    if mark is not None:
        m = mark.reindex(bars.index)
        mh, ml = m["high"].fillna(bars["high"]).to_numpy(float), m["low"].fillna(bars["low"]).to_numpy(float)
    else:
        mh, ml = h, l
    side, stop, target, max_bars = (sig[k].to_numpy(float) for k in ("side", "stop", "target", "max_bars"))
    days = bars.index.floor("D")
    last_of_day = np.append(days[1:] != days[:-1], True)
    slip = SLIPPAGE.get(symbol, 0.001) * cost_mult
    fee = TAKER_FEE * cost_mult
    times = bars.index
    rates = np.zeros(n)
    if funding is not None and len(funding):
        stamp = funding.index.floor("h")
        rates = funding.groupby(stamp).sum().reindex(times).fillna(0.0).to_numpy(float)

    rows, i = [], 0
    while i < n - 1:
        s = side[i]
        if s == 0 or not math.isfinite(s) or not math.isfinite(stop[i]) or stop[i] <= 0:
            i += 1
            continue
        j = i + 1                                     # fills at the next bar's open
        if last_of_day[i]:                            # never open just to be closed at once by the day rule
            i += 1
            continue
        entry = o[j] * (1 + s * slip)
        stop_px = entry - s * stop[i]
        target_px = entry + s * target[i] if math.isfinite(target[i]) and target[i] > 0 else math.nan
        limit = j + max(int(max_bars[i]) - 1, 0) if math.isfinite(max_bars[i]) else n
        pay = 0.0
        k = j
        while True:
            pay += s * rates[k]                       # positive rate: longs pay
            hit_stop = ml[k] <= stop_px if s > 0 else mh[k] >= stop_px
            hit_target = math.isfinite(target_px) and (h[k] >= target_px if s > 0 else l[k] <= target_px)
            if hit_stop:
                gapped = o[k] <= stop_px if s > 0 else o[k] >= stop_px
                px = (l[k] if s > 0 else h[k]) if gapped else stop_px * (1 - s * slip)
                reason = "stop_gap" if gapped else "stop"
                break
            if hit_target:
                px, reason = target_px, "target"
                break
            if last_of_day[k] or k >= limit or k == n - 1:
                px, reason = c[k] * (1 - s * slip), "day_end" if last_of_day[k] else "time"
                break
            k += 1
        gross = s * (px - entry) / entry
        net = gross - 2 * fee - pay
        stop_pct = stop[i] / entry
        notional = min(RISK_PER_TRADE / stop_pct, MAX_NOTIONAL)
        rows.append((symbol, times[j], times[k], s, gross, net, notional * net, net / stop_pct, reason))
        i = k + 1 if k > i else i + 1
    return pd.DataFrame(rows, columns=["symbol", "entry_time", "exit_time", "side", "gross", "net", "pnl", "r", "reason"])


def run(data: dict, sigs: dict[str, pd.DataFrame], cost_mult: float = 1.0) -> Result:
    """All symbols together. `data`: symbol -> dict with bars, mark, funding."""
    frames = [simulate(s, d["bars"], sigs[s], d.get("mark"), d.get("funding"), cost_mult) for s, d in data.items()]
    trades = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    start, end = (min(d["bars"].index[0] for d in data.values()).floor("D"),
                  max(d["bars"].index[-1] for d in data.values()).floor("D"))
    index = pd.date_range(start, end, freq="D")
    daily = (trades.groupby(trades["exit_time"].dt.floor("D"))["pnl"].sum() if len(trades) else pd.Series(dtype=float))
    return Result(trades, daily.reindex(index).fillna(0.0))
