"""Live futures prices over WebSocket: klines and mark price, with auto-reconnect.

URL: <ws base>/market/stream?streams=btcusdt@kline_1h/btcusdt@markPrice/...
(Binance moved market streams to the /market path; the old /stream path no
longer delivers kline or mark price data on live since 2026-04-23.)

Reconnect rules:
- any error or close -> reconnect with growing pause (1, 2, 4 ... max 60 s)
- no message for 30 s -> treat the connection as dead and reconnect
  (mark price arrives every 3 s, so silence means something is wrong)
- Binance closes every connection after 24 h; that is just another reconnect
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

import aiohttp

log = logging.getLogger(__name__)

SILENCE_TIMEOUT_S = 30
MAX_BACKOFF_S = 60


@dataclass(frozen=True)
class ClosedCandle:
    symbol: str
    interval: str
    open_time: datetime          # UTC
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class MarkPrice:
    symbol: str
    mark_price: Decimal
    funding_rate: Decimal        # e.g. 0.0001 = 0.01% per 8h
    next_funding_time: datetime  # UTC


def stream_url(ws_base: str, symbols: list[str], interval: str) -> str:
    names = []
    for s in symbols:
        names += [f"{s.lower()}@kline_{interval}", f"{s.lower()}@markPrice"]
    return f"{ws_base}/market/stream?streams=" + "/".join(names)


def _ms_to_utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def parse_message(msg: dict) -> ClosedCandle | MarkPrice | None:
    """Turn one combined-stream message into a ClosedCandle, a MarkPrice, or None.

    Kline messages arrive every ~250 ms while the candle is still forming;
    only the final one (k.x == true) becomes a ClosedCandle.
    """
    data = msg.get("data", msg)
    event = data.get("e")
    if event == "kline":
        k = data["k"]
        if not k["x"]:
            return None
        return ClosedCandle(
            symbol=data["s"], interval=k["i"], open_time=_ms_to_utc(k["t"]),
            open=Decimal(k["o"]), high=Decimal(k["h"]), low=Decimal(k["l"]),
            close=Decimal(k["c"]), volume=Decimal(k["v"]),
        )
    if event == "markPriceUpdate":
        return MarkPrice(
            symbol=data["s"], mark_price=Decimal(data["p"]),
            funding_rate=Decimal(data["r"]) if data.get("r") else Decimal(0),
            next_funding_time=_ms_to_utc(data["T"]),
        )
    return None


@dataclass
class StreamStats:
    messages: int = 0
    closed_candles: int = 0
    reconnects: int = 0
    last_mark: dict[str, MarkPrice] = field(default_factory=dict)


class MarketStream:
    def __init__(self, ws_base: str, symbols: list[str], interval: str,
                 on_candle: Callable[[ClosedCandle], None] | None = None):
        self.url = stream_url(ws_base, symbols, interval)
        self.on_candle = on_candle
        self.stats = StreamStats()

    async def run(self, stop: asyncio.Event) -> None:
        backoff = 1
        while not stop.is_set():
            try:
                await self._connect_once(stop)
                backoff = 1  # clean close (e.g. 24h limit) -> reconnect right away
            except Exception as e:  # noqa: BLE001 - any failure means: reconnect
                if stop.is_set():
                    break
                log.warning("Market stream error (%s: %s); reconnecting in %ds", type(e).__name__, e, backoff)
                await sleep_or_stop(stop, backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_S)
            if not stop.is_set():
                self.stats.reconnects += 1

    async def _connect_once(self, stop: asyncio.Event) -> None:
        async with aiohttp.ClientSession() as session, session.ws_connect(self.url, heartbeat=20) as ws:
            log.info("Market stream connected: %s", self.url)
            loop = asyncio.get_running_loop()
            last_message = loop.time()
            while not stop.is_set():
                try:
                    # Short timeout so a stop request is noticed within a second.
                    msg = await ws.receive(timeout=1)
                except asyncio.TimeoutError:
                    if loop.time() - last_message > SILENCE_TIMEOUT_S:
                        raise ConnectionError(f"no data for {SILENCE_TIMEOUT_S}s") from None
                    continue
                last_message = loop.time()
                if msg.type != aiohttp.WSMsgType.TEXT:
                    if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        raise ConnectionError(f"websocket closed ({msg.type.name})")
                    continue
                self._handle(msg.json())

    def _handle(self, msg: dict) -> None:
        self.stats.messages += 1
        item = parse_message(msg)
        if isinstance(item, MarkPrice):
            self.stats.last_mark[item.symbol] = item
        elif isinstance(item, ClosedCandle):
            self.stats.closed_candles += 1
            log.info("Closed %s %s candle %s: O=%s H=%s L=%s C=%s V=%s", item.symbol, item.interval,
                     item.open_time.isoformat(), item.open, item.high, item.low, item.close, item.volume)
            if self.on_candle:
                self.on_candle(item)


async def sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), seconds)
    except asyncio.TimeoutError:
        pass
