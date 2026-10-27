"""Technical indicators, computed with pandas-ta.

Strategies call these small functions instead of pandas-ta directly. If pandas-ta
ever breaks (it is a beta release installed with --no-deps, see
requirements-nodeps.txt), only this file needs to change.

All functions take a candle DataFrame (open, high, low, close, volume columns,
oldest row first) and return a pandas Series aligned to it.

Warm-up: an indicator needs `length` candles of history before its value means
anything. pandas-ta returns numbers earlier for some indicators (RSI shows 100.0
on the 2nd candle!), so every function here blanks the first `length` rows with
NaN. A strategy can then simply skip rows that are NaN.
"""

from __future__ import annotations

import warnings

import pandas as pd

# pandas-ta 0.4.71b0 predates pandas 3 and triggers harmless deprecation warnings.
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import pandas_ta as ta


def _warm_up(series: pd.Series, length: int) -> pd.Series:
    series = series.copy()
    series.iloc[:length] = float("nan")
    return series


def ema(candles: pd.DataFrame, length: int) -> pd.Series:
    """Exponential moving average of the close - reacts faster than a simple average."""
    return _warm_up(ta.ema(candles["close"], length=length), length - 1)


def rsi(candles: pd.DataFrame, length: int = 14) -> pd.Series:
    """Relative Strength Index, 0-100. Above ~70 'overbought', below ~30 'oversold'."""
    return _warm_up(ta.rsi(candles["close"], length=length), length)


def atr(candles: pd.DataFrame, length: int = 14) -> pd.Series:
    """Average True Range: typical candle size in price units. Useful for placing
    stop-losses relative to volatility (e.g. stop = entry - 2 * ATR)."""
    return _warm_up(ta.atr(candles["high"], candles["low"], candles["close"], length=length), length)
