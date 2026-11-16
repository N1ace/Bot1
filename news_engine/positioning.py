"""How big traders are positioned in Nasdaq-100 futures, from the CFTC's weekly report. Report-only.

The CFTC publishes every Friday, for positions held the Tuesday before. "Leveraged funds" are mostly hedge funds and
CTAs, "asset managers" are pension and mutual funds. When leveraged funds are more one-sided than on 90% of weeks in
the last 3 years, that is unusual, which is often read as a sign a move may reverse. Nothing trades on this until
months of records show it matters. No key is needed.
"""

import json
from datetime import datetime, timedelta

import requests

URL = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"  # Traders in Financial Futures, futures only
MARKET = "NASDAQ-100 Consolidated - CHICAGO MERCANTILE EXCHANGE"
YEARS = 3
CROWDED_RANK = 0.90
TIMEOUT = 30


def fetch(now: datetime) -> list[dict]:
    start = (now - timedelta(days=YEARS * 365)).strftime("%Y-%m-%d")
    response = requests.get(URL, timeout=TIMEOUT, params={
        "$where": f"market_and_exchange_names = '{MARKET}' AND report_date_as_yyyy_mm_dd >= '{start}'",
        "$order": "report_date_as_yyyy_mm_dd", "$limit": 1000,
        "$select": "report_date_as_yyyy_mm_dd,open_interest_all,lev_money_positions_long,lev_money_positions_short,"
                   "asset_mgr_positions_long,asset_mgr_positions_short"})
    response.raise_for_status()
    return response.json()


def net_share(row: dict, group: str) -> float:
    """Net long as a share of all open contracts. Negative means net short."""
    return (float(row[f"{group}_positions_long"]) - float(row[f"{group}_positions_short"])) / float(row["open_interest_all"])


def summarize(rows: list[dict]) -> dict | None:
    if len(rows) < 52:
        return None
    funds = [net_share(r, "lev_money") for r in rows]
    today = funds[-1]
    rank = sum(v <= today for v in funds[:-1]) / (len(funds) - 1)
    # "Unusual", not "crowded": funds are often near flat, so a top rank can still be a small net long
    label = "unusually long" if rank >= CROWDED_RANK else "unusually short" if rank <= 1 - CROWDED_RANK else "normal"
    return {"report_date": rows[-1]["report_date_as_yyyy_mm_dd"][:10], "funds_net_pct": round(today * 100, 1),
            "funds_rank_3y": round(rank, 3), "managers_net_pct": round(net_share(rows[-1], "asset_mgr") * 100, 1),
            "label": label}


def update(db, now: datetime) -> str:
    """Save the latest week. Returns its label, or a short failure note."""
    try:
        found = summarize(fetch(now))
    except (requests.RequestException, KeyError, ValueError, ZeroDivisionError) as e:
        return f"failed: {type(e).__name__}"
    if found is None:
        return "too little history"
    db.execute("INSERT OR REPLACE INTO positioning VALUES (?, ?)", (found["report_date"], json.dumps(found)))
    db.commit()
    return found["label"]
