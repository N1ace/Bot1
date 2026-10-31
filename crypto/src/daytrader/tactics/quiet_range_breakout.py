"""Trade the move out of an unusually quiet stretch, when volume confirms it."""

from __future__ import annotations

import numpy as np
import pandas as pd

from cryptobot import indicators

HYPOTHESIS = ("Volatility comes in clusters: quiet periods end in bursts, and stops and leveraged orders pile "
              "up just outside a tight range. A break of that range on heavy volume should carry on for some "
              "hours, and this should last because volatility clustering is a stable feature of markets.")

# quiet_hours: 12 hours is roughly one full trading session without a real move.
# quiet_ratio: recent hourly ranges at 70% of the weekly norm are clearly below normal, not merely average.
# volume_mult: 1.5 times the day's average volume shows real participation in the break.
# k_stop: 1.5 weekly ATR. The stop uses the weekly norm because the expected move is back to normal
#         volatility, and a stop based on the quiet period would be too close to costs.
SETTINGS = {"quiet_hours": 12, "quiet_ratio": 0.7, "volume_mult": 1.5, "k_stop": 1.5}
TUNABLE = ("quiet_hours", "quiet_ratio", "volume_mult", "k_stop")

NORMAL_HOURS = 168          # one week defines normal volatility
VOLUME_HOURS = 24           # one day defines normal volume
REWARD_RISK = 2.0           # target is twice the stop, so a win pays for two losses
LAST_ENTRY_HOUR = 20        # later entries have too little of the UTC day left to reach a target


def signals(bars, s):
    n = s["quiet_hours"]
    normal = indicators.atr(bars, NORMAL_HOURS)
    # Measured up to the previous bar, so the breakout bar can't spoil its own quiet reading.
    quiet = (indicators.atr(bars, n) / normal).shift(1) < s["quiet_ratio"]
    heavy = bars["volume"] > s["volume_mult"] * bars["volume"].rolling(VOLUME_HOURS).mean().shift(1)

    broke_high = bars["close"] > bars["high"].rolling(n).max().shift(1)
    broke_low = bars["close"] < bars["low"].rolling(n).min().shift(1)

    side = pd.Series(0.0, index=bars.index)
    side[quiet & heavy & broke_high] = 1.0
    side[quiet & heavy & broke_low] = -1.0
    side[bars.index.hour > LAST_ENTRY_HOUR] = 0.0

    stop = s["k_stop"] * normal
    return pd.DataFrame({"side": side, "stop": stop, "target": REWARD_RISK * stop, "max_bars": 24.0},
                        index=bars.index).replace([np.inf, -np.inf], np.nan)
