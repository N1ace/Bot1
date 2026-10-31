"""Data health check: the Level 0 exit gate needs runs "without crashes or data gaps"."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from cryptobot.backtest.data import MarketDataStore
from cryptobot.observer.positioning import METRICS, load_positioning

CHECK_DAYS = 60   # look for missing days in this recent window


@dataclass
class DataHealth:
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def check(store: MarketDataStore, symbols: list[str], market_db: str, now: pd.Timestamp) -> DataHealth:
    """`now` in UTC. The last closed daily bar must be yesterday's."""
    health = DataHealth()
    yesterday = now.floor("D") - pd.Timedelta(days=1)
    for s in symbols:
        for kind in ("trade", "mark"):
            bars = store.bars(s, kind)
            if bars.empty:
                health.problems.append(f"{s} {kind} candles: none stored")
                continue
            if bars.index[-1] < yesterday:
                health.problems.append(f"{s} {kind} candles: last is {bars.index[-1]:%Y-%m-%d}, expected "
                                       f"{yesterday:%Y-%m-%d}")
            recent = bars.index[bars.index >= yesterday - pd.Timedelta(days=CHECK_DAYS)]
            expected = pd.date_range(yesterday - pd.Timedelta(days=CHECK_DAYS), yesterday, freq="D", tz="UTC")
            missing = expected.difference(recent)
            if len(missing):
                health.problems.append(f"{s} {kind} candles: {len(missing)} missing day(s) in the last "
                                       f"{CHECK_DAYS}, e.g. {missing[0]:%Y-%m-%d}")
        funding = store.funding(s)
        if funding.empty or funding.index[-1] < now - pd.Timedelta(hours=9):
            last = funding.index[-1].strftime("%Y-%m-%d %H:%M") if len(funding) else "never"
            health.problems.append(f"{s} funding: last settlement {last} UTC (expected within 9h)")
        for metric in METRICS:
            series = load_positioning(market_db, s, metric)
            if series.empty or series.index[-1] < yesterday - pd.Timedelta(days=1):
                last = series.index[-1].strftime("%Y-%m-%d") if len(series) else "never"
                health.problems.append(f"{s} {metric}: last point {last}")
    return health
