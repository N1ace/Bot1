"""Market state ("regime") per symbol and for the whole market (strategy-risk-spec.md section 4).

Only uses CLOSED daily bars, funding history and stored positioning data.
Labels follow the spec thresholds:
  trend vs chop : ER(20) > 0.30 or ADX(14) > 25 -> trend;  ER < 0.15 or ADX < 18 -> chop
  volatility    : 30-day realised vol percentile vs the last 365 days; > 90% high, < 10% low
  market state  : BTC close vs its 200-day average + direction of that average over 20 days
  crowding      : funding z-score (90 days) > 2 AND open interest up > 10% in 7 days
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from cryptobot import indicators
from cryptobot.backtest.data import MarketDataStore
from cryptobot.observer.positioning import load_positioning
from cryptobot.strategies.donchian import ensemble_signal


@dataclass(frozen=True)
class SymbolRegime:
    symbol: str
    date: pd.Timestamp              # day of the last closed bar (UTC)
    close: float
    change_1d: float
    change_30d: float
    er20: float
    adx14: float
    trend_label: str                # trend / chop / neutral
    vol30_ann: float
    vol_percentile: float           # 0-1 vs the last 365 days
    vol_label: str                  # high / normal / low
    funding_rate: float             # last settlement
    funding_interval_h: float
    funding_ann: float              # last rate as a yearly cost for a long (e.g. 0.11 = 11%)
    funding_7d_ann: float           # average of the last 7 days, per year
    funding_z90: float
    oi_change_7d: float             # NaN if not enough stored history yet
    crowded: bool
    long_short_ratio: float         # all accounts, NaN if unknown
    signal: float                   # Donchian ensemble: share of the 7 windows that are long


@dataclass(frozen=True)
class MarketRegime:
    date: pd.Timestamp
    btc_close: float
    btc_sma200: float
    sma200_rising: bool
    state: str                      # risk-on / risk-off / mixed
    corr_btc_eth_60d: float


def _interval_hours(funding: pd.DataFrame) -> float:
    known = funding["interval_hours"].dropna()
    if len(known):
        return float(known.iloc[-1])
    gaps = funding.index.to_series().diff().dropna().tail(6)
    return round(gaps.median().total_seconds() / 3600) if len(gaps) else 8.0


def symbol_regime(symbol: str, bars: pd.DataFrame, funding: pd.DataFrame, market_db: str) -> SymbolRegime:
    close = bars["close"]
    last = bars.index[-1]
    er = indicators.efficiency_ratio(bars, 20).iloc[-1]
    adx = indicators.adx(bars, 14).iloc[-1]
    if er > 0.30 or adx > 25:
        trend = "trend"
    elif er < 0.15 or adx < 18:
        trend = "chop"
    else:
        trend = "neutral"

    log_ret = np.log(close).diff()
    vol30 = log_ret.rolling(30).std() * math.sqrt(365)
    window = vol30.dropna().tail(365)
    vol_pct = float((window <= window.iloc[-1]).mean()) if len(window) else math.nan
    vol_label = "high" if vol_pct > 0.90 else "low" if vol_pct < 0.10 else "normal"

    interval = _interval_hours(funding)
    per_year = 24 / interval * 365
    rate = float(funding["rate"].iloc[-1]) if len(funding) else math.nan
    daily = funding["rate"].groupby(funding.index.floor("D")).sum()
    f7 = daily.tail(7).mean() * 365 if len(daily) else math.nan
    hist = daily.tail(91).iloc[:-1]
    z = (daily.iloc[-1] - hist.mean()) / hist.std() if len(hist) > 30 and hist.std() > 0 else math.nan

    oi = load_positioning(market_db, symbol, "openInterestHist")
    oi_change = (oi.iloc[-1] / oi.iloc[-8] - 1) if len(oi) >= 8 else math.nan
    ls = load_positioning(market_db, symbol, "globalLongShortAccountRatio")
    crowded = bool(z > 2 and oi_change > 0.10) if not (math.isnan(z) or math.isnan(oi_change)) else False

    return SymbolRegime(
        symbol=symbol, date=last, close=float(close.iloc[-1]),
        change_1d=float(close.iloc[-1] / close.iloc[-2] - 1), change_30d=float(close.iloc[-1] / close.iloc[-31] - 1),
        er20=float(er), adx14=float(adx), trend_label=trend,
        vol30_ann=float(vol30.iloc[-1]), vol_percentile=vol_pct, vol_label=vol_label,
        funding_rate=rate, funding_interval_h=interval, funding_ann=rate * per_year, funding_7d_ann=float(f7),
        funding_z90=float(z), oi_change_7d=float(oi_change), crowded=crowded,
        long_short_ratio=float(ls.iloc[-1]) if len(ls) else math.nan,
        signal=float(ensemble_signal(close).iloc[-1]),
    )


def market_regime(btc: pd.DataFrame, eth: pd.DataFrame | None) -> MarketRegime:
    sma = indicators.sma(btc, 200)
    rising = bool(sma.iloc[-1] > sma.iloc[-21])
    above = bool(btc["close"].iloc[-1] > sma.iloc[-1])
    state = "risk-on" if above and rising else "risk-off" if not above and not rising else "mixed"
    corr = math.nan
    if eth is not None:
        r = pd.DataFrame({"b": np.log(btc["close"]).diff(), "e": np.log(eth["close"]).diff()}).dropna().tail(60)
        corr = float(r["b"].corr(r["e"]))
    return MarketRegime(date=btc.index[-1], btc_close=float(btc["close"].iloc[-1]), btc_sma200=float(sma.iloc[-1]),
                        sma200_rising=rising, state=state, corr_btc_eth_60d=corr)


def compute(store: MarketDataStore, symbols: list[str], market_db: str) -> tuple[dict[str, SymbolRegime], MarketRegime]:
    bars = {s: store.bars(s, "trade") for s in symbols}
    regimes = {s: symbol_regime(s, bars[s], store.funding(s), market_db) for s in symbols}
    btc = bars.get("BTCUSDT")
    if btc is None:
        btc = store.bars("BTCUSDT", "trade")
    return regimes, market_regime(btc, bars.get("ETHUSDT"))
