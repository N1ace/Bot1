"""User data stream: live account events (orders, fills, balances, algo/stop orders).

How it works:
1. REST: create a listenKey (POST /fapi/v1/listenKey). Valid 60 minutes.
2. WebSocket: connect to <ws base>/private/ws/<listenKey>.
3. Every ~30 minutes: keepalive (PUT) extends the key by 60 minutes.
   If Binance says -1125 (key does not exist) -> create a new key and reconnect.
4. A `listenKeyExpired` event also means: new key, reconnect.
5. Binance closes connections after 24 h -> reconnect.

Every event is stored in SQLite as received (stream_events table). Funding
payments (ACCOUNT_UPDATE with reason FUNDING_FEE) are also written to the
funding_payments table for tax records.

The listenKey gives read access to account events, so it is treated like a
secret: masked in logs, never printed.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from typing import Callable

import aiohttp

from cryptobot.data.market_stream import MAX_BACKOFF_S, sleep_or_stop
from cryptobot.exchange.futures_client import LISTEN_KEY_NOT_FOUND, FuturesClient, api_error_code
from cryptobot.logging_setup import register_secret
from cryptobot.storage import TradeStore

log = logging.getLogger(__name__)

KEEPALIVE_EVERY_S = 30 * 60
HANDLED_EVENTS = ("ORDER_TRADE_UPDATE", "ACCOUNT_UPDATE", "ALGO_UPDATE")


def funding_rows(event: dict) -> list[tuple[str, float]]:
    """(asset, amount) pairs from an ACCOUNT_UPDATE caused by a funding payment."""
    account = event.get("a") or {}
    if event.get("e") != "ACCOUNT_UPDATE" or account.get("m") != "FUNDING_FEE":
        return []
    return [(b["a"], float(b["bc"])) for b in account.get("B", []) if float(b.get("bc", 0)) != 0]


class UserStream:
    def __init__(self, client: FuturesClient, store: TradeStore,
                 on_event: Callable[[dict], None] | None = None):
        self.client = client
        self.store = store
        self.on_event = on_event
        self.counts: Counter[str] = Counter()
        self.reconnects = 0
        self.keepalives = 0
        self._listen_key: str | None = None
        self._need_new_key = asyncio.Event()

    async def run(self, stop: asyncio.Event) -> None:
        keepalive = asyncio.create_task(self._keepalive_loop(stop))
        backoff = 1
        try:
            while not stop.is_set():
                try:
                    await self._new_listen_key()
                    await self._connect_once(stop)
                    backoff = 1
                except Exception as e:  # noqa: BLE001 - any failure means: reconnect
                    if stop.is_set():
                        break
                    log.warning("User stream error (%s: %s); reconnecting in %ds", type(e).__name__, e, backoff)
                    await sleep_or_stop(stop, backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF_S)
                if not stop.is_set():
                    self.reconnects += 1
        finally:
            keepalive.cancel()

    async def _new_listen_key(self) -> None:
        self._listen_key = await asyncio.to_thread(self.client.create_listen_key)
        register_secret(self._listen_key)
        self._need_new_key.clear()
        log.info("User stream listenKey ready")

    async def _connect_once(self, stop: asyncio.Event) -> None:
        url = f"{self.client.ws_url}/private/ws/{self._listen_key}"
        async with aiohttp.ClientSession() as session, session.ws_connect(url, heartbeat=20) as ws:
            log.info("User stream connected (%s/private/ws/***)", self.client.ws_url)
            while not stop.is_set() and not self._need_new_key.is_set():
                try:
                    msg = await ws.receive(timeout=1)  # account events are rare; no silence timeout
                except asyncio.TimeoutError:
                    continue
                if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    raise ConnectionError(f"websocket closed ({msg.type.name})")
                if msg.type == aiohttp.WSMsgType.TEXT:
                    self._handle(msg.json())

    def _handle(self, event: dict) -> None:
        kind = event.get("e", "unknown")
        self.counts[kind] += 1
        self.store.record_stream_event(event)
        for asset, amount in funding_rows(event):
            self.store.record_funding(event.get("E", 0), event.get("S"), asset, amount)
            log.info("Funding payment %s %s %s", event.get("S"), amount, asset)
        if kind == "listenKeyExpired":
            log.warning("listenKey expired - creating a new one")
            self._need_new_key.set()
        elif kind in HANDLED_EVENTS:
            log.info("User event %s", kind)
        else:
            log.info("User event %s (stored, not handled yet)", kind)
        if self.on_event:
            self.on_event(event)

    async def _keepalive_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await sleep_or_stop(stop, KEEPALIVE_EVERY_S)
            if stop.is_set():
                return
            try:
                await asyncio.to_thread(self.client.keepalive_listen_key)
                self.keepalives += 1
                log.info("listenKey keepalive sent")
            except Exception as e:  # noqa: BLE001 - keepalive must never kill the bot
                if api_error_code(e) == LISTEN_KEY_NOT_FOUND:
                    log.warning("listenKey no longer exists (-1125) - recreating")
                    self._need_new_key.set()
                else:
                    log.warning("listenKey keepalive failed (%s: %s)", type(e).__name__, e)
