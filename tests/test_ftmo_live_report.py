import json
from datetime import datetime

import ftmo.live_report as report

BAR = {"open": 27_000.0, "spread": 145}  # 1.45 index points


def fill(kind, price, reason=3):
    return {"type": kind, "price": price, "reason": reason}


def test_costs_are_positive_when_the_fill_is_worse():
    assert report.compare(fill(0, 27_002.0), BAR, None)["live"] == 2.0  # bought above the bar's open
    assert report.compare(fill(1, 26_998.0), BAR, None)["live"] == 2.0  # sold below it
    assert report.compare(fill(1, 27_003.0), BAR, None)["live"] == -3.0
    assert abs(report.compare(fill(0, 27_000.0), BAR, None)["backtest"] - 1.0875) < 1e-9


def test_a_stop_fill_is_compared_with_the_stop():
    c = report.compare(fill(1, 26_895.0, reason=report.DEAL_REASON_SL), BAR, 26_900.0)
    assert c["by_stop"] and c["reference"] == 26_900.0 and c["live"] == 5.0


def test_entry_stops_are_matched_to_positions(tmp_path):
    rows = [{"event": "order_intent", "request": {"sl": 26_900.0}}, {"event": "order_reply", "reply": {"order": 5}},
            {"event": "order_intent", "request": {"position": 5}}, {"event": "order_reply", "reply": {"order": 6}},
            {"event": "deal", "deal": {"symbol": "US100.cash", "position_id": 5}}, {"event": "deal", "deal": {"symbol": ""}}]
    path = tmp_path / "j.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    deals, stops = report.read_journal(path)
    assert stops == {5: 26_900.0} and len(deals) == 1


def test_session_hours_use_server_time():
    assert report.in_session(datetime(2026, 11, 5, 17, 0))  # 10:00 New York
    assert not report.in_session(datetime(2026, 11, 5, 1, 5))  # overnight
    assert not report.in_session(datetime(2026, 11, 7, 17, 0))  # Saturday
