"""Compare each live demo fill with the price the backtest would have used. Read-only: it never sends an order.

    python -m ftmo.live_report                # the demo account's journal
    python -m ftmo.live_report --login 123456

The backtest fills a trade at the open of the minute the check happens, and charges 0.75 of that minute's spread.
Live fills pay the real spread and any slippage. If live costs keep running well above the backtest's, the edge is
smaller than the lab measured, which only live trading can show.
"""

import argparse
import json
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5

from ftmo import backtest
from ftmo.bot import DEAL_REASON_SL, SESSION_OPEN, SYMBOL, file_stem

BACKTEST_SPREAD_SHARE = 0.5 + backtest.SLIPPAGE_SHARE


def read_journal(path) -> tuple[list[dict], dict[int, float]]:
    """This bot's fills, and the broker stop sent with each entry order."""
    deals, stops, sent_stop = [], {}, None
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["event"] == "deal" and row["deal"].get("symbol") == SYMBOL:
            deals.append(row["deal"])
        elif row["event"] == "order_intent":
            sent_stop = row["request"].get("sl")  # only entries carry a stop
        elif row["event"] == "order_reply" and row.get("reply") and sent_stop is not None:
            stops[row["reply"]["order"]] = sent_stop  # an entry's order number becomes its position's number
            sent_stop = None
    return deals, stops


def in_session(server_time: datetime) -> bool:
    ny = server_time - timedelta(hours=backtest.SERVER_HOURS_AHEAD_OF_NY)
    minute = ny.hour * 60 + ny.minute - SESSION_OPEN
    return ny.weekday() < 5 and 0 <= minute < backtest.SESSION_MINUTES


def compare(deal: dict, bar, stop: float | None) -> dict:
    """Points lost to costs, live against backtest. Positive means it cost us."""
    buy = deal["type"] == 0
    by_stop = deal["reason"] == DEAL_REASON_SL
    reference = stop if by_stop and stop else float(bar["open"])  # MT5 bars are bid prices, like the backtest's
    live = (deal["price"] - reference) if buy else (reference - deal["price"])
    assumed = BACKTEST_SPREAD_SHARE * float(bar["spread"]) * 0.01  # MT5 stores the spread in points of 0.01
    return {"reference": reference, "live": live, "backtest": assumed, "by_stop": by_stop}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login", type=int, default=1515150087)
    args = parser.parse_args()
    path = file_stem(args.login, True).with_suffix(".journal.jsonl")
    deals, stops = read_journal(path)
    if not deals:
        print("No demo fills yet.")
        return
    if not mt5.initialize():
        raise SystemExit(f"Can't reach MT5: {mt5.last_error()}")
    try:
        rows, extra_dollars, results = [], 0.0, 0.0
        for deal in deals:
            when = datetime.fromtimestamp(deal["time"], timezone.utc)  # MT5 server time
            minute = when.replace(second=0, microsecond=0)
            bars = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M1, minute, minute + timedelta(seconds=59))
            if deal["entry"] == 1:
                results += deal["profit"] + deal["swap"] + deal["commission"] + deal["fee"]
            if bars is None or len(bars) == 0:
                rows.append(f"{when:%Y-%m-%d %H:%M} server  no bar found")
                continue
            c = compare(deal, bars[0], stops.get(deal["position_id"]))
            side = "buy " if deal["type"] == 0 else "sell"
            kind = "open " if deal["entry"] == 0 else "close"
            note = "broker stop" if c["by_stop"] else ""
            if not in_session(when.replace(tzinfo=None)):
                note = "outside US hours, left out of the totals"
            else:
                extra_dollars += (c["live"] - c["backtest"]) * deal["volume"]
            rows.append(f"{when:%Y-%m-%d %H:%M} {kind} {side} {deal['volume']:<5} fill {deal['price']:>9.2f}  "
                        f"backtest {c['reference']:>9.2f}  cost live {c['live']:>6.2f}  backtest {c['backtest']:>5.2f} "
                        f"points  {note}")
        counted = [r for r in rows if "left out" not in r and "no bar" not in r]
        print("Server time        side       lots  live and backtest prices, costs in index points")
        print("\n".join(rows))
        print(f"\nFills counted: {len(counted)} of {len(rows)}")
        print(f"Live costs above the backtest's, in dollars: {extra_dollars:+.2f}  (positive means live cost more)")
        print(f"Closed trades' result, with swap: {results:+.2f} USD")
        if len(counted) < 30:
            print("Fewer than 30 fills: too few to judge. Keep collecting.")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
