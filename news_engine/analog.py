"""Estimate the likely size of a move from past events of the same type.

Every estimate uses only events that came before it. Only move size is used, never direction.

    python -m news_engine.analog refresh    # fetch event dates and measure reactions
    python -m news_engine.analog evaluate   # compare with the simple baseline
"""

import argparse

import numpy as np
import pandas as pd

from news_engine import events, reactions, store

QUANTILES = (0.25, 0.5, 0.75)
HALF_LIFE_DAYS = 3 * 365
MIN_PAST_EVENTS = 8
BASELINE_EVENTS = 12


def weighted_quantiles(values: np.ndarray, weights: np.ndarray, quantiles=QUANTILES) -> np.ndarray:
    order = np.argsort(values)
    cumulative = np.cumsum(weights[order]) - 0.5 * weights[order]
    return np.interp(np.array(quantiles) * weights.sum(), cumulative, values[order])


def pinball(outcome: float, estimates: np.ndarray, quantiles=QUANTILES) -> float:
    """Lower is better: the standard score for a range estimate."""
    gaps = outcome - estimates
    return float(np.mean([max(q * g, (q - 1) * g) for q, g in zip(quantiles, gaps)]))


def analog_estimate(past: pd.DataFrame, when: pd.Timestamp, vol: float) -> np.ndarray:
    """Past moves, scaled to how volatile the market is now, with recent events counting more."""
    age = (when - past["when"]).dt.days.to_numpy()
    weights = 0.5 ** (age / HALF_LIFE_DAYS)
    return weighted_quantiles((past["size"] / past["pre_vol_pct"]).to_numpy() * vol, weights)


def baseline_estimate(past: pd.DataFrame) -> np.ndarray:
    """B1: the plain spread of the last 12 events."""
    return np.quantile(past["size"].tail(BASELINE_EVENTS).to_numpy(), QUANTILES)


def walk_forward(frame: pd.DataFrame) -> dict | None:
    """Score each event using only earlier ones. Frame needs: when, size, pre_vol_pct."""
    frame = frame.sort_values("when").reset_index(drop=True)
    analog_loss, base_loss, inside_50 = [], [], []
    for i in range(MIN_PAST_EVENTS, len(frame)):
        past, now = frame.iloc[:i], frame.iloc[i]
        estimate = analog_estimate(past, now["when"], now["pre_vol_pct"])
        analog_loss.append(pinball(now["size"], estimate))
        base_loss.append(pinball(now["size"], baseline_estimate(past)))
        inside_50.append(estimate[0] <= now["size"] <= estimate[2])
    if not analog_loss:
        return None
    return {"scored": len(analog_loss), "analog_loss": float(np.mean(analog_loss)),
            "baseline_loss": float(np.mean(base_loss)), "inside_25_75": float(np.mean(inside_50))}


def evaluate(db) -> None:
    query = """SELECT e.type, r.symbol, r.window, e.scheduled_utc AS scheduled, ABS(r.return_pct) AS size, r.pre_vol_pct
               FROM reactions r JOIN events e ON e.id = r.event_id WHERE r.pre_vol_pct IS NOT NULL"""
    data = pd.read_sql_query(query, db)
    data["when"] = pd.to_datetime(data["scheduled"], utc=True)
    print("type   symbol       window scored analog  baseline better  inside 25-75 (target 50%)")
    for (kind, symbol, window), part in data.groupby(["type", "symbol", "window"]):
        score = walk_forward(part)
        if score:
            better = 1 - score["analog_loss"] / score["baseline_loss"]
            print(f"{kind:6} {symbol:12} {window:6} {score['scored']:5} {score['analog_loss']:7.3f} "
                  f"{score['baseline_loss']:7.3f} {better:6.1%}  {score['inside_25_75']:6.0%}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["refresh", "evaluate"])
    args = parser.parse_args()
    db = store.connect()
    if args.command == "refresh":
        rows = events.fomc_events()
        key = events.fred_key()
        if key:
            rows += events.fred_events(key)
        else:
            print("No FRED_API_KEY, so CPI and NFP are skipped.")
        events.save_events(db, rows)
        print(f"{len(rows)} events, {reactions.record_all(db)} measurements")
    else:
        evaluate(db)


if __name__ == "__main__":
    main()
