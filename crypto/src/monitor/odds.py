"""Prediction-market odds for the briefing. Context only: zero weight in any decision.

Polymarket's public read API, no key. Not sent to the AI summariser.
"""

from __future__ import annotations

import json
import logging

import requests

log = logging.getLogger(__name__)

EVENTS = "https://gamma-api.polymarket.com/events"


def top_events(max_events: int, http: requests.Session | None = None) -> list[str]:
    """The busiest open crypto markets as text lines. Empty if unavailable."""
    http = http or requests.Session()
    try:
        resp = http.get(EVENTS, params={"closed": "false", "limit": max_events, "tag_slug": "crypto",
                                        "order": "volume24hr", "ascending": "false"}, timeout=20)
        resp.raise_for_status()
        return [line for line in map(_line, resp.json()) if line]
    except (requests.RequestException, ValueError) as e:
        log.warning("Polymarket unavailable: %s", e)
        return []


def _line(event: dict) -> str | None:
    """The event's most-traded market, with its 'Yes' price as a percentage."""
    try:
        market = max(event.get("markets", []), key=lambda m: float(m.get("volume24hr") or 0))
        yes = float(json.loads(market["outcomePrices"])[0])
        return f"{market['question']}: {yes:.0%}"
    except (KeyError, ValueError, TypeError, IndexError):
        return None
