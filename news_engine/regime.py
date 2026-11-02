"""A simple daily picture of market conditions, from free FRED data. Reduce-only, and for the briefing until proven.

The multiplier can only suggest less risk, never more. Rules were set before looking at any result, and nothing reads the
multiplier yet: the spec keeps it out of trading until it has earned a place.
"""

import json
from datetime import datetime, timedelta

from news_engine import events

OBSERVATIONS = "https://api.stlouisfed.org/fred/series/observations"
SERIES = {"vix": "VIXCLS", "curve_10y_2y": "T10Y2Y", "sp500": "SP500"}
LOOKBACK_DAYS = 420
AVERAGE_DAYS = 200

STRESS_VIX, HIGH_VIX = 30.0, 20.0


def classify(vix: float | None, curve: float | None, above_average: bool | None) -> tuple[str, float]:
    """Label and risk multiplier. Missing data counts as caution, never as calm."""
    if vix is None:
        return "unknown", 0.5
    multiplier = 0.25 if vix >= STRESS_VIX else 0.5 if vix >= HIGH_VIX else 1.0
    label = "stress" if vix >= STRESS_VIX else "high_volatility" if vix >= HIGH_VIX else "calm"
    if above_average is False:
        multiplier = max(0.25, multiplier * 0.75)
        label += "_downtrend"
    if curve is not None and curve < 0:
        label += "_inverted_curve"
    return label, multiplier


def values(key: str, series: str, now: datetime) -> list[float]:
    start = (now - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    data = events.fred_get(OBSERVATIONS, {"series_id": series, "api_key": key, "file_type": "json",
                                          "observation_start": start})
    return [float(row["value"]) for row in data["observations"] if row["value"] != "."]


def update(db, now: datetime) -> str:
    """Save today's label. Returns it, or a short failure note."""
    key = events.fred_key()
    if not key:
        return "no FRED key"
    try:
        data = {name: values(key, series, now) for name, series in SERIES.items()}
    except RuntimeError as e:
        return str(e)
    vix = data["vix"][-1] if data["vix"] else None
    curve = data["curve_10y_2y"][-1] if data["curve_10y_2y"] else None
    prices = data["sp500"]
    above = prices[-1] > sum(prices[-AVERAGE_DAYS:]) / AVERAGE_DAYS if len(prices) >= AVERAGE_DAYS else None
    label, multiplier = classify(vix, curve, above)
    details = {"vix": vix, "curve_10y_2y": curve, "sp500_above_200_day": above}
    db.execute("INSERT OR REPLACE INTO regime VALUES (?, ?, ?, ?)", (f"{now:%Y-%m-%d}", label, multiplier, json.dumps(details)))
    db.commit()
    return label
