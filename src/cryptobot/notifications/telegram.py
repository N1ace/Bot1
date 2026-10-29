"""Telegram: send reports to the owner and read the owner's /approve, /reject commands.

Setup: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env (see README).
Security:
- the token is a secret: registered with the log masker, never printed
- messages are only sent to the owner's chat id
- commands are only accepted from the owner's chat id; anything else is ignored
"""

from __future__ import annotations

import logging
import os

import requests

from cryptobot.logging_setup import register_secret

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4000   # Telegram's limit is 4096 characters per message


class TelegramNotifier:
    def __init__(self, enabled: bool = False, token: str | None = None, chat_id: str | None = None):
        self.token = (token if token is not None else os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip()
        self.chat_id = str(chat_id if chat_id is not None else os.environ.get("TELEGRAM_CHAT_ID", "")).strip()
        self.enabled = enabled and bool(self.token) and bool(self.chat_id)
        if self.token:
            register_secret(self.token)
        if enabled and not self.enabled:
            log.warning("Telegram enabled in config but TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing in .env")

    def _call(self, method: str, **params) -> dict:
        try:
            resp = requests.post(API.format(token=self.token, method=method), data=params, timeout=20)
            return resp.json()
        except Exception as e:  # noqa: BLE001 - never crash the bot because Telegram is down
            # don't log the exception text: it can contain the URL with the token
            log.warning("Telegram %s failed (%s)", method, type(e).__name__)
            return {"ok": False}

    def send(self, text: str) -> bool:
        """Send to the owner. Long texts are split. Returns True if every part was delivered."""
        if not self.enabled:
            log.info("[telegram off] %s", text[:200])
            return False
        ok = True
        for i in range(0, len(text), MAX_LEN):
            ok &= bool(self._call("sendMessage", chat_id=self.chat_id, text=text[i:i + MAX_LEN]).get("ok"))
        return ok

    def owner_commands(self, offset: int) -> tuple[int, list[str]]:
        """New messages from the OWNER since `offset`. Returns (next offset, texts)."""
        if not self.enabled:
            return offset, []
        data = self._call("getUpdates", offset=offset, timeout=0)
        texts, next_offset = [], offset
        for upd in data.get("result", []) if data.get("ok") else []:
            next_offset = max(next_offset, upd["update_id"] + 1)
            msg = upd.get("message") or {}
            if str((msg.get("chat") or {}).get("id")) != self.chat_id:
                log.warning("Ignored a Telegram message from an unknown chat")
                continue
            if msg.get("text"):
                texts.append(msg["text"].strip())
        return next_offset, texts
