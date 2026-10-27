"""Sanity checks that pandas-ta works with our pinned numba/pandas versions."""

import numpy as np
import pandas as pd

from cryptobot import indicators


def candles(n=200):
    close = pd.Series(np.linspace(100, 200, n) + np.sin(np.arange(n)) * 5)
    return pd.DataFrame({"open": close, "high": close + 2, "low": close - 2, "close": close, "volume": 1.0})


def test_ema_matches_plain_pandas():
    # pandas-ta seeds the EMA with a simple average, so values differ by a tiny amount.
    df = candles()
    expected = df["close"].ewm(span=20, adjust=False).mean()
    assert abs(indicators.ema(df, 20).iloc[-1] - expected.iloc[-1]) < 1e-6


def test_warm_up_rows_are_nan():
    df = candles()
    assert indicators.ema(df, 20).iloc[:19].isna().all()
    assert indicators.rsi(df, 14).iloc[:14].isna().all()
    assert indicators.atr(df, 14).iloc[:14].isna().all()
    assert indicators.rsi(df, 14).iloc[14:].notna().all()


def test_rsi_in_range():
    assert indicators.rsi(candles(), 14).dropna().between(0, 100).all()


def test_atr_positive():
    assert (indicators.atr(candles(), 14).dropna() > 0).all()
