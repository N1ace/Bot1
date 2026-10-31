"""Big-move alerts: a background thread on Binance's public market stream.

Watches 1-minute BTC candles between the hourly checks, and adds up liquidations from
`!forceOrder@arr`. Binance pushes only the largest liquidation per symbol per second, so the
totals undercount; they are compared only with our own logs, which makes that consistent.

Public data only, no key. Both streams work on the /market/stream route (tested 2026-10-30).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import aiohttp

from monitor.flags import STREAM_KEY, mark_ok
from monitor.market import log_liquidation_hour

log = logging.getLogger(__name__)

STREAM = "wss://fstream.binance.com/market/stream?streams=btcusdt@kline_1m/!forceOrder@arr"


class MoveWatcher:
    """Remembers an hour of 1-minute closes and reports a move past the limit, at most once per cooldown."""

    def __init__(self, limit: float, cooldown_minutes: float):
        self.limit = limit
        self.cooldown = timedelta(minutes=cooldown_minutes)
        self.closes: deque[tuple[datetime, float]] = deque()
        self.last_alert: datetime | None = None

    def update(self, when: datetime, close: float) -> float | None:
        """Returns the 1-hour return when it should alert, else None."""
        self.closes.append((when, close))
        while self.closes and when - self.closes[0][0] > timedelta(minutes=60):
            self.closes.popleft()
        if when - self.closes[0][0] < timedelta(minutes=30):
            return None                               # not enough history yet
        move = close / self.closes[0][1] - 1
        if abs(move) <= self.limit:
            return None
        if self.last_alert is not None and when - self.last_alert < self.cooldown:
            return None
        self.last_alert = when
        return move


class LiquidationCounter:
    """Adds up USDT liquidated in the current hour and saves it as the hour goes on."""

    def __init__(self, db: str | Path):
        self.db = db
        self.hour: datetime | None = None
        self.total = 0.0
        self.last_save: datetime | None = None

    def add(self, when: datetime, notional: float) -> None:
        hour = when.replace(minute=0, second=0, microsecond=0)
        if self.hour is not None and hour != self.hour:
            log_liquidation_hour(self.db, self.hour, self.total)
            self.total = 0.0
        self.hour = hour
        self.total += notional
        if self.last_save is None or when - self.last_save >= timedelta(minutes=1):
            log_liquidation_hour(self.db, hour, self.total)
            self.last_save = when


def handle_message(raw: str, watcher: MoveWatcher, liq: LiquidationCounter) -> float | None:
    """Process one stream message. Returns a move to alert on, if any."""
    msg = json.loads(raw)
    data = msg.get("data", {})
    if data.get("e") == "kline":
        k = data["k"]
        if k.get("x"):                                # closed candles only
            when = datetime.fromtimestamp(k["T"] / 1000, tz=timezone.utc)
            return watcher.update(when, float(k["c"]))
    elif data.get("e") == "forceOrder":
        o = data["o"]
        when = datetime.fromtimestamp(data["E"] / 1000, tz=timezone.utc)
        liq.add(when, float(o["z"]) * float(o["ap"]))
    return None


async def _run(db: str | Path, limit: float, cooldown: float, on_move: Callable[[float], None]) -> None:
    watcher, liq = MoveWatcher(limit, cooldown), LiquidationCounter(db)
    delay = 5
    proved = 0.0
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(STREAM, heartbeat=30) as ws:
                    log.info("Market stream connected")
                    delay = 5
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:                  # one bad message must not end the watcher
                                if time.monotonic() - proved >= 60:
                                    mark_ok(db, STREAM_KEY)
                                    proved = time.monotonic()
                                move = handle_message(msg.data, watcher, liq)
                                if move is not None:
                                    await asyncio.to_thread(on_move, move)
                            except Exception:     # noqa: BLE001 - logged, and the stream keeps going
                                log.exception("Could not handle a stream message")
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
        except Exception as e:  # noqa: BLE001 - this thread must never die, it just reconnects
            log.warning("Market stream dropped: %s: %s", type(e).__name__, e)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 300)


def run_forever(db: str | Path, limit: float, cooldown: float, on_move: Callable[[float], None]) -> None:
    asyncio.run(_run(db, limit, cooldown, on_move))
