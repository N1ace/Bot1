from datetime import datetime, timezone

import pytest

import ftmo.watchdog as watchdog
from ftmo.bot import NEW_YORK

NOW = datetime(2026, 11, 9, 14, 45, tzinfo=timezone.utc)  # Monday 09:45 New York


@pytest.fixture
def sent(monkeypatch, tmp_path):
    texts = []
    monkeypatch.setattr(watchdog.telegram, "send", lambda text: texts.append(text) or "sent")
    monkeypatch.setattr(watchdog, "SENT_FILE", tmp_path / "ok.txt")
    return texts


def ny(*args):
    return datetime(*args, tzinfo=NEW_YORK)


def test_watches_only_around_the_session():
    assert watchdog.watching(ny(2026, 11, 9, 9, 0)) and watchdog.watching(ny(2026, 11, 9, 15, 59))
    assert not watchdog.watching(ny(2026, 11, 9, 8, 59)) and not watchdog.watching(ny(2026, 11, 9, 16, 0))
    assert not watchdog.watching(ny(2026, 11, 7, 10, 0))  # Saturday
    assert not watchdog.watching(ny(2026, 11, 26, 10, 0))  # Thanksgiving


def test_problems():
    assert watchdog.problems(NOW, True, 0.5) == []
    assert watchdog.problems(NOW, False, 0.5) == ["MT5 isn't running"]
    assert "hasn't updated for 10 minutes" in watchdog.problems(NOW, True, 10)[0]
    assert "never run" in watchdog.problems(NOW, True, None)[0]


def run_at(monkeypatch, utc, mt5_up=True, quiet=0.5):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return utc
    monkeypatch.setattr(watchdog, "datetime", Clock)
    monkeypatch.setattr(watchdog, "mt5_running", lambda: mt5_up)
    monkeypatch.setattr(watchdog, "bot_minutes_quiet", lambda now: quiet)
    watchdog.main()


def test_all_running_is_sent_once_a_day(monkeypatch, sent):
    run_at(monkeypatch, NOW)
    run_at(monkeypatch, NOW.replace(minute=15, hour=15))
    assert sent == ["FTMO: bot and MT5 running for today's session"]


def test_a_down_bot_warns_every_run(monkeypatch, sent):
    run_at(monkeypatch, NOW, quiet=40)
    run_at(monkeypatch, NOW.replace(hour=15), quiet=70)
    assert len(sent) == 2 and all(text.startswith("FTMO WARNING") for text in sent)


def test_quiet_outside_hours(monkeypatch, sent):
    run_at(monkeypatch, datetime(2026, 11, 9, 23, 0, tzinfo=timezone.utc), mt5_up=False, quiet=None)
    assert sent == []


def test_a_running_dry_run_cannot_hide_a_dead_demo_bot(monkeypatch, tmp_path):
    import os
    monkeypatch.setattr(watchdog, "BOT_DIR", tmp_path)
    demo, dry = tmp_path / "1_demo.state.json", tmp_path / "1_dry.state.json"
    demo.write_text("{}"), dry.write_text("{}")
    os.utime(demo, (NOW.timestamp() - 3600, NOW.timestamp() - 3600))
    os.utime(dry, (NOW.timestamp(), NOW.timestamp()))
    assert round(watchdog.bot_minutes_quiet(NOW)) == 60
