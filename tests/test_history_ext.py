"""Extended history joins spot -> futures prices and assumed -> Bybit -> Binance funding correctly."""

import sqlite3

import pandas as pd

from cryptobot.backtest.data import MarketDataStore
from cryptobot.backtest.history_ext import ASSUMED_RATE, ExtendedHistory


def ms(ts):
    return int(pd.Timestamp(ts, tz="UTC").timestamp() * 1000)


def test_join(tmp_path):
    db = str(tmp_path / "m.sqlite")
    store = MarketDataStore(db)
    rows = [("BTCUSDT", "spot", ms(d), 1, 1, 1, 1, 1) for d in pd.date_range("2017-08-17", "2020-01-05")]
    rows += [("BTCUSDT", "trade", ms(d), 2, 2, 2, 2, 2) for d in pd.date_range("2020-01-01", "2020-01-10")]
    store._db.executemany("INSERT INTO bars VALUES (?,?,?,?,?,?,?,?)", rows)
    store._db.executemany("INSERT INTO funding VALUES (?,?,?,?)",
                          [("BTCUSDT", ms("2020-01-01 00:00"), 8, 0.0003)])
    store._db.commit()
    ext = ExtendedHistory(store, db)
    with sqlite3.connect(db) as con:
        con.executemany("INSERT INTO funding_ext VALUES (?,?,?,?)", [
            ("BTCUSDT", ms("2018-11-15 00:00"), 0.0002, "bybit_inverse"),
            ("BTCUSDT", ms("2019-09-10 08:00"), -0.0001, "binance_rest"),
            ("BTCUSDT", ms("2019-09-10 16:00"), 0.0005, "bybit_inverse"),   # after Binance starts: must be dropped
        ])

    bars = ext.bars("BTCUSDT")
    assert bars.loc["2019-12-31", "close"] == 1                  # spot before futures
    assert bars.loc["2020-01-01", "close"] == 2                  # futures win on overlap
    assert bars.index.is_unique and bars.index.min() == pd.Timestamp("2017-08-17", tz="UTC")

    f = ext.funding("BTCUSDT")
    assert f.loc[f.index < pd.Timestamp("2018-11-15", tz="UTC"), "source"].eq("assumed").all()
    assert (f.loc[f["source"] == "assumed", "rate"] == ASSUMED_RATE).all()
    assert list(f.loc["2018-11-15":"2020-01-01", "source"]) == ["bybit_inverse", "binance_rest", "binance"]
    assert f.index.is_monotonic_increasing
