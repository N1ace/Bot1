"""Level 1 paper trading: level gate, no order code, append-only record, replay check."""

import ast
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from cryptobot.levels import LevelError
from cryptobot.paper.runner import (PaperResult, PaperSettingsChanged, check_lock, run_paper, save_equity,
                                    sync_fills, sync_funding)
from cryptobot.storage.db import TradeStore


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "t.sqlite")
    TradeStore(path, "demo", "futures", "FUTURES").close()
    return path


def fills(price=100.0):
    return pd.DataFrame([{"date": pd.Timestamp("2026-11-02", tz="UTC"), "symbol": "BTCUSDT", "qty": 0.5, "price": price,
                          "fee": 0.1, "realized_pnl": 0.0, "reason": "signal"}])


def test_refused_below_level_1():
    with pytest.raises(LevelError):
        run_paper(0, ":memory:")


def test_paper_code_cannot_reach_the_exchange():
    root = Path(__file__).parent.parent / "src" / "cryptobot"
    for folder in ("paper", "strategies", "backtest", "risk"):
        for path in (root / folder).glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
                if isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                bad = [n for n in names if any(w in n for w in ("exchange", "execution", "modes", "ccxt", "binance"))]
                assert not bad, f"{path} imports {bad}"


def test_fills_are_appended_once_and_kept_separate(db):
    assert sync_fills(db, "core", fills()) == (1, [])
    assert sync_fills(db, "core", fills()) == (0, [])
    assert sync_fills(db, "xs", fills())[0] == 1
    con = sqlite3.connect(db)
    assert con.execute("SELECT environment, side, quantity FROM fills ORDER BY environment").fetchall() == [
        ("paper-core", "buy", 0.5), ("paper-xs", "buy", 0.5)]


def test_two_fills_of_the_same_kind_on_one_day_both_stay(db):
    two = pd.concat([fills(), fills(101.0)], ignore_index=True)
    assert sync_fills(db, "core", two)[0] == 2


def test_replay_that_changes_history_raises_a_warning(db):
    sync_fills(db, "core", fills())
    added, problems = sync_fills(db, "core", fills(price=101.0))
    assert added == 0 and len(problems) == 1 and "changed on replay" in problems[0]


def test_equity_saved_per_sleeve(db):
    eq = pd.Series([100.0, 101.0], index=pd.date_range("2026-11-02", periods=2, tz="UTC"))
    save_equity(db, "core", eq)
    save_equity(db, "core", eq)
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM paper_equity").fetchone()[0] == 2


def test_report_text_shows_kill_switch_and_warnings():
    text = PaperResult(equity={"core": 31_000.0}, start_equity={"core": 30_000.0}, killed=["core"],
                       problems=["x"]).text()
    assert "+3.3%" in text and "KILL SWITCH" in text and "WARNING: x" in text


def test_report_before_the_start_date_is_a_plain_message():
    assert "starts 2026-11-01" in PaperResult(waiting_for="2026-11-01").text()


def test_a_vanished_fill_is_reported_and_kept(db):
    sync_fills(db, "core", fills())
    added, problems = sync_fills(db, "core", fills().iloc[0:0])
    assert added == 0 and len(problems) == 1 and "vanished" in problems[0]
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1


def test_a_changed_fee_is_reported(db):
    sync_fills(db, "core", fills())
    assert "changed on replay" in sync_fills(db, "core", fills().assign(fee=0.5))[1][0]


def test_funding_is_recorded_once_under_the_paper_environment(db):
    from datetime import datetime, timezone
    from cryptobot.reporting.trades import load_funding
    pay = pd.DataFrame([{"time": pd.Timestamp("2026-11-02 08:00", tz="UTC"), "symbol": "BTCUSDT", "amount": -1.5}])
    assert sync_funding(db, "core", pay) == 1
    assert sync_funding(db, "core", pay) == 0
    start, end = datetime(2026, 11, 1, tzinfo=timezone.utc), datetime(2026, 11, 3, tzinfo=timezone.utc)
    got = load_funding(db, start, end)
    assert list(got["environment"]) == ["paper-core"] and got["amount"].sum() == -1.5


def lock_settings(tmp_path, capital=50000):
    core, xs = tmp_path / "core.yaml", tmp_path / "xs.yaml"
    if not core.exists():
        core.write_text("# note\nrisk: 1\n")
        xs.write_text("risk: 2\n")
    return {"start_date": "2026-11-01", "capital": capital, "core_config": str(core), "xs_config": str(xs)}


def test_settings_are_locked_but_comments_may_change(db, tmp_path):
    s = lock_settings(tmp_path)
    check_lock(db, s)
    check_lock(db, s)
    Path(s["core_config"]).write_text("# a different note\nrisk: 1\n")
    check_lock(db, s)
    with pytest.raises(PaperSettingsChanged):
        check_lock(db, {**s, "start_date": "2026-12-01"})
    with pytest.raises(PaperSettingsChanged):
        check_lock(db, lock_settings(tmp_path, capital=20000))
    Path(s["xs_config"]).write_text("risk: 3\n")
    with pytest.raises(PaperSettingsChanged):
        check_lock(db, s)
