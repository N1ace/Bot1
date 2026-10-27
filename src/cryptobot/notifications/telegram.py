"""Telegram alerts - STUB. For now messages are only written to the log.

Later: create a bot with @BotFather, put TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
in .env, and send via https://api.telegram.org/bot<token>/sendMessage.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, enabled: bool = False):
        self.enabled = enabled

    def send(self, text: str) -> None:
        # Stub: always log. Real sending is added later.
        log.info("[telegram%s] %s", "" if self.enabled else " disabled", text)
