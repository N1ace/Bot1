"""Day-trade lab: pessimistic fills, and rules that can't be bent."""

import numpy as np
import pandas as pd
import pytest

from daytrader import lab, sim


def bars(rows, start="2022-01-03"):
    """rows: (open, high, low, close)."""
    idx = pd.date_range(start, periods=len(rows), freq="h", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx, dtype=float)


def one_signal(b, side=1, stop=5.0, target=np.nan, max_bars=100, at=0):
    sig = pd.DataFrame({"side": 0.0, "stop": stop, "target": target, "max_bars": max_bars}, index=b.index)
    sig.iloc[at, 0] = side
    return sig


FLAT = (100, 101, 99, 100)


def test_entry_is_the_next_open_with_costs():
    b = bars([FLAT, (110, 111, 109, 110), (110, 111, 109, 110)] + [FLAT] * 3)
    t = sim.simulate("BTCUSDT", b, one_signal(b, stop=50, max_bars=2))
    assert t.iloc[0]["entry_time"] == b.index[1]
    assert t.iloc[0]["net"] < 0                  # flat trade still pays fees and slippage


def test_stop_wins_when_stop_and_target_share_a_bar():
    b = bars([FLAT, FLAT, (100, 120, 80, 100), FLAT])
    t = sim.simulate("BTCUSDT", b, one_signal(b, stop=5, target=5))
    assert t.iloc[0]["reason"] == "stop"


def test_gap_past_the_stop_fills_at_the_bar_extreme():
    b = bars([FLAT, FLAT, (80, 82, 70, 75), FLAT])
    t = sim.simulate("BTCUSDT", b, one_signal(b, stop=5))
    assert t.iloc[0]["reason"] == "stop_gap" and t.iloc[0]["gross"] == pytest.approx(70 / 100.03 - 1, rel=1e-3)


def test_position_is_closed_by_the_end_of_the_utc_day():
    b = bars([FLAT] * 30)
    t = sim.simulate("BTCUSDT", b, one_signal(b, stop=50, max_bars=1000))
    assert t.iloc[0]["reason"] == "day_end" and t.iloc[0]["exit_time"] == b.index[23]


def test_long_pays_positive_funding_short_receives():
    b = bars([FLAT] * 6)
    funding = pd.Series([0.01], index=[b.index[2]])
    long_t = sim.simulate("BTCUSDT", b, one_signal(b, side=1, stop=50, max_bars=3), funding=funding)
    short_t = sim.simulate("BTCUSDT", b, one_signal(b, side=-1, stop=50, max_bars=3), funding=funding)
    assert long_t.iloc[0]["net"] < -0.009 and short_t.iloc[0]["net"] > 0.005


def test_double_costs_cost_more():
    b = bars([FLAT] * 6)
    sig = one_signal(b, stop=50, max_bars=2)
    assert sim.simulate("BTCUSDT", b, sig, cost_mult=2)["net"].iloc[0] < sim.simulate("BTCUSDT", b, sig)["net"].iloc[0]


def test_notional_is_capped():
    b = bars([FLAT] * 6)
    t = sim.simulate("BTCUSDT", b, one_signal(b, stop=0.01, max_bars=2))
    assert abs(t.iloc[0]["pnl"]) <= abs(t.iloc[0]["net"]) * sim.MAX_NOTIONAL + 1e-12


# ---- rules ------------------------------------------------------------------------------------

def write(tmp_path, text):
    path = tmp_path / "t.py"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("code", ["import requests\n", "import os\n", "from cryptobot import exchange\n" * 0 + "import sqlite3\n",
                                  "x = open('data/x')\n", "def f(s):\n    return s.shift(-1)\n",
                                  "def f(s):\n    return s.shift(periods=-3)\n", "x = ().__class__\n"])
def test_unsafe_tactics_are_refused(tmp_path, code):
    with pytest.raises(lab.LabRefused):
        lab.check_code(write(tmp_path, code))


def test_clean_tactic_passes_the_code_check(tmp_path):
    lab.check_code(write(tmp_path, "import numpy as np\nimport pandas as pd\n\ndef f(s):\n    return s.shift(1)\n"))


class Peeking:
    SETTINGS = {}

    @staticmethod
    def signals(b, s):
        sig = pd.DataFrame({"side": 0.0, "stop": 1.0, "target": np.nan, "max_bars": 5}, index=b.index)
        sig["side"] = np.sign(b["close"].iloc[::-1].rolling(3).mean().iloc[::-1] - b["close"]).fillna(0)
        return sig


class Honest:
    SETTINGS = {}

    @staticmethod
    def signals(b, s):
        sig = pd.DataFrame({"side": 0.0, "stop": 1.0, "target": np.nan, "max_bars": 5}, index=b.index)
        sig["side"] = np.sign(b["close"] - b["close"].rolling(3).mean()).fillna(0)
        return sig


def walk(n=2000):
    rng = np.random.default_rng(1)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    return {"BTCUSDT": {"bars": bars(np.column_stack([c, c * 1.01, c * 0.99, c]))}}


def test_lookahead_is_caught_even_without_a_negative_shift():
    with pytest.raises(lab.LabRefused):
        lab.check_no_lookahead(Peeking, walk())
    lab.check_no_lookahead(Honest, walk())


def rows(*names, hash_="a"):
    return [{"name": n, "hash": hash_, "stage": "test", "sharpe": "0.1", "passed": "False"} for n in names]


def test_a_tactic_gets_one_test_and_a_change_needs_a_new_name():
    with pytest.raises(lab.LabRefused, match="already tested"):
        lab.check_budget("x", "a", rows("x"))
    with pytest.raises(lab.LabRefused, match="new name"):
        lab.check_budget("x", "b", rows("x"))


def test_budget_stops_the_search():
    used = rows(*[f"t{i}" for i in range(lab.MAX_TACTICS)])
    with pytest.raises(lab.LabRefused, match="Budget"):
        lab.check_budget("new", "a", used)
    lab.check_budget("new", "a", used[:-1])


def test_hidden_data_is_cut_from_research():
    idx = pd.date_range("2025-04-30", periods=72, freq="h", tz="UTC")
    data = {"BTCUSDT": {"bars": pd.DataFrame({"close": 1.0}, index=idx)}}
    assert lab.research_part(data)["BTCUSDT"]["bars"].index.max() < lab.HOLDOUT_START


def test_every_attempt_counts_in_the_luck_score():
    got = lab.trials(rows("a", "b"), [1.0, 2.0])
    assert len(got) == 4


def test_final_needs_a_pass_and_runs_once(tmp_path, monkeypatch):
    path = write(tmp_path, "x = 1\n")
    monkeypatch.setattr(lab, "get_tactic", lambda name: (object(), path))
    digest = lab.fingerprint(path)
    log = tmp_path / "log.csv"
    with pytest.raises(lab.LabRefused, match="pass"):
        lab.run_final("t", {}, log)
    base = dict.fromkeys(lab.COLUMNS, "")
    lab.write_log({**base, "name": "t", "hash": digest, "stage": "test", "passed": True}, log)
    lab.write_log({**base, "name": "t", "hash": digest, "stage": "final", "passed": False}, log)
    with pytest.raises(lab.LabRefused, match="one look"):
        lab.run_final("t", {}, log)


def test_neighbours_move_each_tunable_setting():
    class M:
        SETTINGS = {"n": 10, "k": 2.0, "fixed": 3}
        TUNABLE = ("n", "k")
    got = lab.neighbours(M)
    assert len(got) == 4 and got["n 8"]["n"] == 8 and got["k 2.4"]["k"] == pytest.approx(2.4)
    assert all(v["fixed"] == 3 for v in got.values())
