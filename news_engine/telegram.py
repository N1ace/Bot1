"""Send the briefing to the owner's Telegram. Plain text, and errors never show the address, because it holds the token."""

from pathlib import Path

import requests

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
TIMEOUT = 20
MAX_LENGTH = 4000  # Telegram's limit is 4,096 characters


def settings() -> tuple[str, str] | None:
    if not ENV_FILE.exists():
        return None
    values = dict(line.split("=", 1) for line in ENV_FILE.read_text(encoding="utf-8").splitlines()
                  if "=" in line and not line.startswith("#"))
    token, chat = values.get("TELEGRAM_BOT_TOKEN", "").strip(), values.get("TELEGRAM_CHAT_ID", "").strip()
    return (token, chat) if token and chat else None


def split_text(text: str) -> list[str]:
    """Split on line ends so a long briefing stays readable."""
    parts, current = [], ""
    for line in text.splitlines():
        line = line[:MAX_LENGTH]
        if len(current) + len(line) + 1 > MAX_LENGTH:
            parts.append(current)
            current = ""
        current += line + "\n"
    return parts + [current] if current else parts


def send(text: str) -> str:
    found = settings()
    if not found:
        return "not set up"
    token, chat = found
    for part in split_text(text):
        try:
            response = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                     json={"chat_id": chat, "text": part}, timeout=TIMEOUT)
        except requests.RequestException as e:
            return f"failed: {type(e).__name__}"
        if not response.ok:
            return f"failed: status {response.status_code}"
    return "sent"
