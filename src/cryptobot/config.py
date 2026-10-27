"""Load config/config.yaml + .env, validate it, and enforce the live-trading guard.

Design decision: config is turned into frozen (read-only) dataclasses right at
startup. A typo or a dangerous combination fails immediately with a clear message,
instead of half-way through a trading session.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import yaml

MODES = ("spot", "margin", "futures", "copytrade_lead")
ENVIRONMENTS = ("paper", "testnet", "live")

# Live trading needs this exact value in the environment, on top of the config flag.
LIVE_CONFIRM_ENV = "CRYPTOBOT_CONFIRM_LIVE"
LIVE_CONFIRM_VALUE = "I_UNDERSTAND_REAL_MONEY"


class ConfigError(ValueError):
    """Raised when the configuration is invalid or unsafe."""


@dataclass(frozen=True)
class RiskConfig:
    max_order_value: float
    max_open_positions: int
    max_daily_loss: float
    require_stop_loss: bool = True


@dataclass(frozen=True)
class Credentials:
    api_key: str
    api_secret: str

    def __repr__(self) -> str:
        # Never show real keys, even when someone prints this object while debugging.
        return "Credentials(api_key=***, api_secret=***)"


@dataclass(frozen=True)
class Config:
    mode: str
    environment: str
    live_trading: bool
    account: str
    quote_currency: str
    pairs: list[str]
    timeframe: str
    candle_limit: int
    risk: RiskConfig
    paper_balances: dict[str, float] = field(default_factory=dict)
    log_level: str = "INFO"
    log_dir: str = "logs"
    sqlite_path: str = "data/cryptobot.sqlite"
    telegram_enabled: bool = False


def load_config(path: str | Path, env: Mapping[str, str] | None = None) -> Config:
    """Read the YAML file, build a Config, and run all safety checks."""
    env = os.environ if env is None else env
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    cfg = _build(raw)
    check_live_guard(cfg, env)
    return cfg


def _build(raw: dict) -> Config:
    try:
        risk_raw = raw["risk"]
        risk = RiskConfig(
            max_order_value=float(risk_raw["max_order_value"]),
            max_open_positions=int(risk_raw["max_open_positions"]),
            max_daily_loss=float(risk_raw["max_daily_loss"]),
            require_stop_loss=bool(risk_raw.get("require_stop_loss", True)),
        )
        cfg = Config(
            mode=str(raw["mode"]),
            environment=str(raw.get("environment", "paper")),
            live_trading=raw.get("live_trading", False) is True,  # only a real YAML true counts
            account=str(raw.get("account", "SPOT")).upper(),
            quote_currency=str(raw.get("quote_currency", "USDT")),
            pairs=list(raw["pairs"]),
            timeframe=str(raw.get("timeframe", "1h")),
            candle_limit=int(raw.get("candle_limit", 100)),
            risk=risk,
            paper_balances={k: float(v) for k, v in raw.get("paper", {}).get("starting_balances", {}).items()},
            log_level=str(raw.get("logging", {}).get("level", "INFO")).upper(),
            log_dir=str(raw.get("logging", {}).get("dir", "logs")),
            sqlite_path=str(raw.get("storage", {}).get("sqlite_path", "data/cryptobot.sqlite")),
            telegram_enabled=bool(raw.get("telegram", {}).get("enabled", False)),
        )
    except KeyError as e:
        raise ConfigError(f"Missing required config key: {e}") from e

    if cfg.mode not in MODES:
        raise ConfigError(f"mode must be one of {MODES}, got {cfg.mode!r}")
    if cfg.environment not in ENVIRONMENTS:
        raise ConfigError(f"environment must be one of {ENVIRONMENTS}, got {cfg.environment!r}")
    if not cfg.pairs:
        raise ConfigError("pairs must list at least one symbol, e.g. BTC/USDT")
    if risk.max_order_value <= 0 or risk.max_open_positions <= 0 or risk.max_daily_loss <= 0:
        raise ConfigError("risk limits must all be positive numbers")
    return cfg


def check_live_guard(cfg: Config, env: Mapping[str, str]) -> None:
    """Live trading requires BOTH the config flag AND the env-var confirmation."""
    if cfg.environment == "live":
        if not cfg.live_trading:
            raise ConfigError("environment is 'live' but live_trading is not true in config.yaml")
        if env.get(LIVE_CONFIRM_ENV) != LIVE_CONFIRM_VALUE:
            raise ConfigError(
                f"Live trading blocked: set {LIVE_CONFIRM_ENV}={LIVE_CONFIRM_VALUE} to confirm real-money trading"
            )
    elif cfg.live_trading:
        # Contradictory settings usually mean a mistake - refuse rather than guess.
        raise ConfigError(f"live_trading is true but environment is {cfg.environment!r}; fix one of them")


def load_credentials(account: str, environment: str, env: Mapping[str, str] | None = None) -> Credentials | None:
    """Read API keys for one account from the environment (.env).

    Testnet and live keys use DIFFERENT variable names, so a testnet run can never
    accidentally pick up live keys. Returns None if the keys are not set.
    """
    env = os.environ if env is None else env
    prefix = f"BINANCE_{account.upper()}_{environment.upper()}"
    key = env.get(f"{prefix}_API_KEY", "").strip()
    secret = env.get(f"{prefix}_API_SECRET", "").strip()
    if not key or not secret:
        return None
    return Credentials(api_key=key, api_secret=secret)


def credential_env_names(account: str, environment: str) -> tuple[str, str]:
    """The variable names we look for - handy for helpful error messages."""
    prefix = f"BINANCE_{account.upper()}_{environment.upper()}"
    return f"{prefix}_API_KEY", f"{prefix}_API_SECRET"
