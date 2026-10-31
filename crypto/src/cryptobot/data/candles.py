"""Fetch CLOSED candles.

Important detail: the newest candle the exchange returns is usually still forming
(e.g. at 14:20 the 14:00 1h candle is only 20 minutes old). Strategies that use it
would see signals appear and disappear. So we fetch one extra candle and drop the
unfinished one.

WebSocket live prices will be added here later. Note: Binance retired the old
listenKey user-data streams in 2026; account updates will use the WebSocket API.
"""

from __future__ import annotations

import pandas as pd

from cryptobot.exchange.base import ExchangeClient

_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


def timeframe_seconds(timeframe: str) -> int:
    """'1h' -> 3600, '15m' -> 900, '1d' -> 86400."""
    amount, unit = timeframe[:-1], timeframe[-1]
    if unit not in _UNIT_SECONDS or not amount.isdigit():
        raise ValueError(f"Unsupported timeframe {timeframe!r}")
    return int(amount) * _UNIT_SECONDS[unit]


def drop_unclosed(df: pd.DataFrame, timeframe: str, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Remove candles whose period has not finished yet."""
    now = now or pd.Timestamp.now(tz="UTC")
    close_times = df.index + pd.Timedelta(seconds=timeframe_seconds(timeframe))
    return df[close_times <= now]


def get_closed_candles(client: ExchangeClient, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    df = client.fetch_ohlcv(symbol, timeframe, limit + 1)
    return drop_unclosed(df, timeframe).tail(limit)
