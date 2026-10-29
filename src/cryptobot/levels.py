"""Bot levels (bot-roadmap-and-principles.md Part 3): what the bot is allowed to do.

The level is set ONLY by the owner in config/config.yaml (`level:`). The bot never
raises it; at most it suggests a level-up in the weekly review. Every action that
could matter checks `require_level()` first and refuses if the level is too low.
"""

from __future__ import annotations

LEVEL_NAMES = {
    0: "Observer (data + reports only)",
    1: "Paper signals",
    2: "Demo execution",
    3: "Live, minimum risk",
    4: "Live, standard risk",
    5: "Second strategy",
    6: "Advanced",
}

# Minimum level for each action.
ACTIONS = {
    "collect_data": 0,
    "report": 0,
    "paper_trade": 1,
    "demo_order": 2,
    "live_order": 3,
}

# Levels whose code exists. Higher levels are refused until they are built and reviewed.
HIGHEST_AVAILABLE_LEVEL = 2

# Level 0 exit gate: this many clean daily runs in a row (no crash, no data gap).
LEVEL0_GATE_DAYS = 14


class LevelError(PermissionError):
    """An action was attempted above the bot's configured level."""


def level_name(level: int) -> str:
    return f"{level} - {LEVEL_NAMES.get(level, 'unknown')}"


def require_level(level: int, action: str) -> None:
    needed = ACTIONS[action]
    if level < needed:
        raise LevelError(
            f"Refused: '{action}' needs level {level_name(needed)}, but the bot is at level {level_name(level)}. "
            f"Only the owner can change the level in config/config.yaml."
        )


def check_level_config(level: int, environment: str) -> str | None:
    """Return an error message if level and environment don't fit together, else None."""
    if not isinstance(level, int) or not 0 <= level <= 6:
        return f"level must be a whole number 0-6, got {level!r}"
    if level > HIGHEST_AVAILABLE_LEVEL:
        return f"level {level_name(level)} is not built yet (highest available: {HIGHEST_AVAILABLE_LEVEL})"
    if environment == "live" and level < 3:
        return f"environment 'live' needs level 3 or higher (bot is at level {level})"
    if level >= 3 and environment != "live":
        return f"level {level} is a live level but environment is {environment!r}"
    return None
