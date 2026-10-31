"""Level 0: regime math, data health, advisor store, tips, approval flow, Telegram safety."""

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from cryptobot import indicators
from cryptobot.advisor.report import approval_reply, daily_report
from cryptobot.advisor.store import AdvisorStore
from cryptobot.advisor.tips import CONCEPTS, Tip, candidates, pick_tip
from cryptobot.notifications.telegram import TelegramNotifier
from cryptobot.observer.daily import handle_commands
from cryptobot.observer.regime import MarketRegime, SymbolRegime, market_regime


def bars_from(closes):
    c = pd.Series(closes, index=pd.date_range("2024-01-01", periods=len(closes), tz="UTC"), dtype=float)
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1.0})


# ---- indicators / regime ------------------------------------------------------------

def test_efficiency_ratio_straight_vs_zigzag():
    straight = bars_from(np.linspace(100, 120, 40))
    zigzag = bars_from([100 + (i % 2) for i in range(40)])
    assert indicators.efficiency_ratio(straight, 20).iloc[-1] == pytest.approx(1.0)
    assert indicators.efficiency_ratio(zigzag, 20).iloc[-1] < 0.1


def test_market_regime_states():
    up = bars_from(np.linspace(100, 300, 260))
    assert market_regime(up, None).state == "risk-on"
    down = bars_from(np.linspace(300, 100, 260))
    assert market_regime(down, None).state == "risk-off"


def regime(symbol="BTCUSDT", **kw):
    base = dict(symbol=symbol, date=pd.Timestamp("2026-10-28", tz="UTC"), close=80_000, change_1d=0.01,
                change_30d=0.05, er20=0.2, adx14=20, trend_label="neutral", vol30_ann=0.5, vol_percentile=0.5,
                vol_label="normal", funding_rate=0.0001, funding_interval_h=8, funding_ann=0.1095,
                funding_7d_ann=0.10, funding_z90=0.5, oi_change_7d=0.02, crowded=False, long_short_ratio=1.8,
                signal=3 / 7)
    return SymbolRegime(**(base | kw))


MARKET = MarketRegime(date=pd.Timestamp("2026-10-28", tz="UTC"), btc_close=80_000, btc_sma200=90_000,
                      sma200_rising=False, state="risk-off", corr_btc_eth_60d=0.82)


# ---- tips ------------------------------------------------------------------------------

def test_tip_priority_data_problems_first():
    tips = candidates({"BTCUSDT": regime()}, MARKET, ["BTCUSDT funding: last settlement never"])
    assert tips[0].concept == "data_health"


def test_high_funding_tip_uses_numbers_and_rule():
    tips = candidates({"ETHUSDT": regime("ETHUSDT", funding_7d_ann=0.44)}, MARKET, [])
    funding = next(t for t in tips if t.concept == "funding")
    assert "44%" in funding.text and "halve" in funding.text


def test_first_time_concept_is_explained_then_not():
    regimes = {"BTCUSDT": regime(trend_label="chop", er20=0.1, adx14=15)}
    first = pick_tip(regimes, MARKET, [], seen=set(), recent=[])
    assert first.concept == "chop" and "(New concept)" in first.text
    again = pick_tip(regimes, MARKET, [], seen={"chop"}, recent=[])
    assert "(New concept)" not in again.text


def test_same_concept_not_repeated_within_3_days_unless_urgent():
    regimes = {"BTCUSDT": regime(trend_label="chop", er20=0.1, adx14=15)}
    assert pick_tip(regimes, MARKET, [], seen={"chop"}, recent=["chop"]).concept != "chop"
    crowded = {"BTCUSDT": regime(crowded=True, funding_z90=2.5, oi_change_7d=0.15)}
    assert pick_tip(crowded, MARKET, [], seen={"crowding"}, recent=["crowding"]).concept == "crowding"


def test_tips_never_suggest_more_risk():
    words = ("increase", "raise", "more risk", "bigger", "add leverage")
    regimes = {"BTCUSDT": regime(vol_label="high", vol_percentile=0.95, trend_label="chop", crowded=True,
                                 funding_7d_ann=0.7, funding_z90=3, oi_change_7d=0.2)}
    for t in candidates(regimes, MARKET, ["x"]) + [Tip(k, v) for k, v in CONCEPTS.items()]:
        assert not any(w in t.text.lower() for w in words), t.text


# ---- advisor store: gate streak, suggestions, approvals ------------------------------------

def test_clean_streak_breaks_on_problem_or_missing_day():
    a = AdvisorStore(":memory:")
    for d in (1, 2, 3):
        a.record_run(date(2026, 10, d), "ok", [])
    assert a.clean_streak(date(2026, 10, 3)) == 3
    a.record_run(date(2026, 10, 4), "data_problems", ["gap"])
    assert a.clean_streak(date(2026, 10, 4)) == 0
    a.record_run(date(2026, 10, 6), "ok", [])            # 10-05 missing
    assert a.clean_streak(date(2026, 10, 6)) == 1


def test_suggestions_limited_to_3_per_week_and_no_duplicates():
    a = AdvisorStore(":memory:")
    assert a.add_suggestion("other", "A", "e", "x: 1") is not None
    assert a.add_suggestion("other", "A", "e", "x: 1") is None          # same one still pending
    a.add_suggestion("other", "B", "e", None)
    a.add_suggestion("other", "C", "e", None)
    assert a.add_suggestion("other", "D", "e", None) is None            # 4th this week


def test_approval_only_records_and_tells_owner_what_to_edit():
    a = AdvisorStore(":memory:")
    sid = a.add_suggestion("level_up", "Go to level 1?", "14 clean days", "config/config.yaml -> level: 1")
    replies = handle_commands(a, [f"/approve {sid}"])
    assert a.suggestion(sid)["status"] == "approved"
    assert "does NOT change settings" in replies[0] and "level: 1" in replies[0]
    assert handle_commands(a, ["/reject 999"]) == ["No suggestion #999."]
    assert "rejected" in approval_reply({"id": 5, "status": "rejected", "config_change": None})


def test_daily_report_contains_all_five_sections():
    text = daily_report(date(2026, 10, 28), 0, {"BTCUSDT": regime()}, MARKET, [], 3, Tip("trend_state", "tip"), [])
    for section in ("1) MARKET", "2) POSITIONS", "3) RISK USAGE", "4) WHAT THE BOT DID", "5) TIP OF THE DAY"):
        assert section in text
    assert "3 of 14 clean days" in text


# ---- telegram safety ------------------------------------------------------------------

def test_telegram_ignores_commands_from_other_chats(monkeypatch):
    n = TelegramNotifier(enabled=True, token="123:abcdefghij", chat_id="5377682705")
    monkeypatch.setattr(n, "_call", lambda method, **p: {"ok": True, "result": [
        {"update_id": 10, "message": {"chat": {"id": 5377682705}, "from": {"id": 5377682705}, "text": "/approve 1"}},
        {"update_id": 11, "message": {"chat": {"id": 999}, "from": {"id": 999}, "text": "/approve 2"}},
        {"update_id": 12, "message": {"chat": {"id": 5377682705}, "from": {"id": 42}, "text": "/approve 3"}},
    ]})
    offset, texts = n.owner_commands(0)
    assert texts == ["/approve 1"] and offset == 13


def test_second_approval_says_already_decided():
    a = AdvisorStore(":memory:")
    sid = a.add_suggestion("other", "X", "e", "x: 1")
    handle_commands(a, [f"/reject {sid}"])
    assert handle_commands(a, [f"/approve {sid}"]) == [f"#{sid} was already rejected - nothing changed."]
    assert a.suggestion(sid)["status"] == "rejected"


def test_telegram_disabled_without_credentials():
    assert TelegramNotifier(enabled=True, token="", chat_id="").enabled is False


def test_a_paper_failure_does_not_lose_the_report_and_is_a_problem(monkeypatch):
    from cryptobot.observer import daily
    import cryptobot.paper.runner as runner

    def boom(*a, **k):
        raise RuntimeError("no data")
    monkeypatch.setattr(runner, "run_paper", boom)

    class Cfg:
        level, sqlite_path = 1, ":memory:"
    text, problems = daily._paper_section(Cfg)
    assert "FAILED" in text and problems == [text]
