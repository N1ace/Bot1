from datetime import datetime, time, timezone

import numpy as np
import pandas as pd

from news_engine import analog, daily, events, reactions, store


def test_new_york_time_follows_the_clock_change():
    assert store.new_york_to_utc("2026-10-28", time(14, 0)).hour == 18
    assert store.new_york_to_utc("2026-11-04", time(14, 0)).hour == 19


def test_server_stamps_become_utc():
    times = pd.Series(pd.to_datetime(["2026-11-02 16:30", "2026-07-01 16:30"]))
    utc = store.server_to_utc(times)
    assert utc.iloc[0].hour == 14 and utc.iloc[1].hour == 13


def test_fomc_parser_uses_the_second_day_and_handles_two_months():
    page = "<h4>2022 FOMC Meetings</h4> January 25-26 April/May 3-4 <p>x</p> 2021 FOMC Meetings March 16-17*"
    assert events.parse_fomc(page) == ["2021-03-17", "2022-01-26", "2022-05-04"]


def test_quantiles_follow_the_weights():
    values, equal = np.array([1.0, 2.0, 3.0]), np.ones(3)
    assert analog.weighted_quantiles(values, equal, (0.5,))[0] == 2.0
    assert analog.weighted_quantiles(values, np.array([0.01, 0.01, 10.0]), (0.5,))[0] > 2.5


def test_pinball_is_zero_only_for_a_perfect_estimate():
    assert analog.pinball(1.0, np.array([1.0, 1.0, 1.0])) == 0
    assert analog.pinball(2.0, np.array([1.0, 1.0, 1.0])) > 0


def bars_around(moment, move):
    times = pd.date_range(moment - pd.Timedelta(minutes=10), periods=80, freq="min")
    close = np.where(times >= moment, 100.0 + move, 100.0)
    return pd.DataFrame({"open": close, "high": close, "low": close, "close": close}, index=times)


def test_reaction_starts_from_the_last_bar_before_the_event():
    moment = pd.Timestamp("2026-10-28 18:00", tz="UTC")
    found = reactions.react(bars_around(moment, 1.0), moment)
    assert np.isclose(found["15m"][0], 1.0) and "4h" not in found


def test_no_reaction_when_the_market_was_closed():
    moment = pd.Timestamp("2026-10-28 18:00", tz="UTC")
    bars = bars_around(moment, 1.0).loc[moment + pd.Timedelta(minutes=1):]
    assert reactions.react(bars, moment) == {}


def test_pre_volatility_ignores_the_event_day_onward():
    index = pd.date_range("2026-01-01 12:00", periods=40, freq="D", tz="UTC")
    calm = 100 + np.resize([0.0, 1.0], 40)
    bars = pd.DataFrame({"close": calm}, index=index)
    moment = index[30]
    changed = bars.copy()
    changed.iloc[30:, 0] = 500.0
    assert reactions.pre_volatility(bars, moment) == reactions.pre_volatility(changed, moment)


def test_walk_forward_estimates_use_only_earlier_events():
    when = pd.date_range("2022-01-01", periods=12, freq="30D", tz="UTC")
    frame = pd.DataFrame({"when": when, "size": 1.0, "pre_vol_pct": 1.0})
    later = frame.copy()
    later.loc[11, "size"] = 50.0
    first = analog.analog_estimate(frame.iloc[:10], when[10], 1.0)
    second = analog.analog_estimate(later.iloc[:10], when[10], 1.0)
    assert np.array_equal(first, second)


def test_walk_forward_needs_enough_past_events():
    when = pd.date_range("2022-01-01", periods=5, freq="30D", tz="UTC")
    assert analog.walk_forward(pd.DataFrame({"when": when, "size": 1.0, "pre_vol_pct": 1.0})) is None


def test_engine_stores_events_and_reactions(tmp_path):
    db = store.connect(tmp_path / "t.sqlite")
    events.save_events(db, [("FOMC-2026-10-28", "FOMC", 1, "2026-10-28T18:00:00+00:00", "test")])
    assert db.execute("SELECT type FROM events").fetchone() == ("FOMC",)


def seeded(tmp_path):
    db = store.connect(tmp_path / "t.sqlite")
    for i in range(12):
        event = f"CPI-{i}"
        when = pd.Timestamp("2025-01-14 13:30", tz="UTC") + pd.Timedelta(days=30 * i)
        db.execute("INSERT INTO events VALUES (?, 'CPI', 1, ?, 't')", (event, when.isoformat()))
        db.execute("INSERT INTO reactions VALUES (?, 'US100.cash', '15m', ?, 0.5, 1.0)", (event, 0.5 + i / 100))
    db.execute("INSERT INTO events VALUES ('CPI-next', 'CPI', 1, '2026-11-10T14:30:00+00:00', 't')")
    db.commit()
    return db


def test_estimates_are_saved_only_before_the_event(tmp_path, monkeypatch):
    monkeypatch.setattr(reactions, "SYMBOLS", ("US100.cash",))
    monkeypatch.setattr(reactions, "WINDOWS", {"15m": 15})
    db = seeded(tmp_path)
    before = datetime(2026, 11, 9, 14, 0, tzinfo=timezone.utc)
    after = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)
    assert daily.predict(db, after, lambda s: 1.0) == 0
    assert daily.predict(db, before, lambda s: 1.0) == 1
    assert daily.predict(db, before, lambda s: 2.0) == 0  # a saved estimate never changes


def test_events_too_far_ahead_wait(tmp_path, monkeypatch):
    monkeypatch.setattr(reactions, "SYMBOLS", ("US100.cash",))
    db = seeded(tmp_path)
    assert daily.predict(db, datetime(2026, 11, 1, tzinfo=timezone.utc), lambda s: 1.0) == 0


def test_score_fills_the_outcome_once_the_reaction_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(reactions, "SYMBOLS", ("US100.cash",))
    monkeypatch.setattr(reactions, "WINDOWS", {"15m": 15})
    db = seeded(tmp_path)
    daily.predict(db, datetime(2026, 11, 9, 14, 0, tzinfo=timezone.utc), lambda s: 1.0)
    assert daily.score(db) == 0
    db.execute("INSERT INTO reactions VALUES ('CPI-next', 'US100.cash', '15m', -0.6, 0.8, 1.0)")
    assert daily.score(db) == 1
    assert daily.score(db) == 0
    assert "Live scored events 1" in daily.scorecard(db)
