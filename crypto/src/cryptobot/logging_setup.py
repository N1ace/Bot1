"""Logging to the console and to rotating files in logs/.

Rotating = when cryptobot.log reaches 5 MB it is renamed to cryptobot.log.1 and a
new file starts; we keep 10 old files. Logs never grow without limit.

A filter masks known secret values, as a safety net in case a key ever ends up in
an error message (some libraries include request details in exceptions).
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


class SecretMaskingFilter(logging.Filter):
    def __init__(self, secrets: Iterable[str] = ()):
        super().__init__()
        self._secrets: list[str] = []
        for s in secrets:
            self.add(s)

    def add(self, secret: str) -> None:
        # Ignore very short values so we don't mask ordinary words by accident.
        if secret and len(secret) >= 8 and secret not in self._secrets:
            self._secrets.append(secret)

    def filter(self, record: logging.LogRecord) -> bool:
        if self._secrets:
            msg = record.getMessage()
            for s in self._secrets:
                msg = msg.replace(s, "***")
            record.msg, record.args = msg, None
        return True


# One shared filter, so secrets created at runtime (e.g. listenKeys) can be added.
_mask = SecretMaskingFilter()


def register_secret(value: str) -> None:
    """Mask this value in all log output from now on."""
    _mask.add(value)


def setup_logging(level: str = "INFO", log_dir: str = "logs", secrets: Iterable[str] = ()) -> None:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(LOG_FORMAT)
    for s in secrets:
        _mask.add(s)

    console = logging.StreamHandler()
    file = RotatingFileHandler(Path(log_dir) / "cryptobot.log", maxBytes=5_000_000, backupCount=10, encoding="utf-8")
    for handler in (console, file):
        handler.setFormatter(formatter)
        handler.addFilter(_mask)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(console)
    root.addHandler(file)
    # Third-party libraries are chatty; keep them to warnings.
    for noisy in ("urllib3", "ConnectorLogger", "aiohttp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
