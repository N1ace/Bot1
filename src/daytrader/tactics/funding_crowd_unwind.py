"""Fade a crowded side of the perpetual market, but only once price starts to go against that crowd."""

from __future__ import annotations

import numpy as np
import pandas as pd

from cryptobot import indicators

HYPOTHESIS = ("When funding is unusually high or low, one side of the market is crowded with leverage, and "
              "the worst cascades start from crowded positioning. Once price breaks against that crowd, forced "
              "exits push it further; this should last because leverage traders keep crowding and being "
              "liquidated, and the fade side also collects funding instead of paying it.")

# funding_days: a month of history defines what "normal" funding is for this coin right now.
# z_min: 2 standard deviations is the usual meaning of "unusual".
# break_hours: 12 hours is half a day; losing that range means the crowd's recent gains are gone.
# k_stop: 1.5 ATR leaves room for an ordinary hourly swing without a wide stop.
SETTINGS = {"funding_days": 30, "z_min": 2.0, "break_hours": 12, "k_stop": 1.5}
TUNABLE = ("funding_days", "z_min", "break_hours", "k_stop")

BASE_FUNDING = 0.0001       # Binance's normal rate of 0.01% per 8h: longs aren't crowded at or below it
REWARD_RISK = 2.0           # target is twice the stop, so a win pays for two losses
ATR_HOURS = 24              # one day of hourly ranges
LAST_ENTRY_HOUR = 20        # later entries have too little of the UTC day left to reach a target


def signals(bars, s):
    funding = bars["funding"]
    window = s["funding_days"] * 24
    z = (funding - funding.rolling(window).mean()) / funding.rolling(window).std()
    crowded_long = (z > s["z_min"]) & (funding > BASE_FUNDING)
    crowded_short = (z < -s["z_min"]) & (funding < 0)

    # The range excludes the current bar: the bar's close has to break what came before it.
    broke_low = bars["close"] < bars["low"].rolling(s["break_hours"]).min().shift(1)
    broke_high = bars["close"] > bars["high"].rolling(s["break_hours"]).max().shift(1)

    side = pd.Series(0.0, index=bars.index)
    side[crowded_long & broke_low] = -1.0
    side[crowded_short & broke_high] = 1.0
    side[bars.index.hour > LAST_ENTRY_HOUR] = 0.0

    stop = s["k_stop"] * indicators.atr(bars, ATR_HOURS)
    return pd.DataFrame({"side": side, "stop": stop, "target": REWARD_RISK * stop, "max_bars": 24.0},
                        index=bars.index).replace([np.inf, -np.inf], np.nan)
