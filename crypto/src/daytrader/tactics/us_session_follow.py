"""Follow a large move built up through the UTC day into the US afternoon."""

from __future__ import annotations

import numpy as np
import pandas as pd

from cryptobot import indicators

HYPOTHESIS = ("Published intraday-momentum work finds that a market's early move tends to continue late in the "
              "session, because slow and rule-based flows trade late. A big move from the UTC day's open to the "
              "US open should keep going into the US afternoon, and this should last because those flows are "
              "tied to the clock rather than to any single piece of information.")

# session_hour: the 13:00 UTC bar closes at 14:00, after the 12:30 UTC US data and the 13:30 UTC stock open.
# min_move: a 14-hour random walk moves about 3 hourly ATRs, so 3 keeps only days with a real move so far.
# k_stop: 2 ATR, wider than a normal hour because the trade is held for several hours.
SETTINGS = {"session_hour": 13, "min_move": 3.0, "k_stop": 2.0}
TUNABLE = ("session_hour", "min_move", "k_stop")

ATR_HOURS = 168             # one week of hourly ranges, so one wild hour doesn't change the scale much


def signals(bars, s):
    atr = indicators.atr(bars, ATR_HOURS)
    day_open = bars["open"].groupby(bars.index.floor("D")).transform("first")
    move = bars["close"] - day_open

    at_hour = bars.index.hour == s["session_hour"]
    side = pd.Series(0.0, index=bars.index)
    side[at_hour & (move > s["min_move"] * atr)] = 1.0
    side[at_hour & (move < -s["min_move"] * atr)] = -1.0

    # No target and no time limit: the trade rides until its stop or the end of the UTC day.
    return pd.DataFrame({"side": side, "stop": s["k_stop"] * atr, "target": np.nan, "max_bars": 24.0},
                        index=bars.index)
