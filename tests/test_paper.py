"""Level 1 paper trading: level gate, no order code, append-only record, replay check."""

import ast
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from cryptobot.levels import LevelError
from cryptobot.paper.runner import PaperResult, run_paper, save_equity, sync_fills
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
    tree = ast.parse(Path("src/cryptobot/paper/runner.py").read_text(encoding="utf-8"))
    imported = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    assert not [m for m in imported if m and ("exchange" in m or "execution" in m)]


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
