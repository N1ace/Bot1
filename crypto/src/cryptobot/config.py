"""Load config/config.yaml + .env, validate it, and enforce the live-trading guard.

Design decision: config is turned into frozen (read-only) dataclasses right at
startup. A typo or a dangerous combination fails immediately with a clear message,
instead of half-way through a trading session.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import yaml

from cryptobot.levels import check_level_config

MODES = ("spot", "margin", "futures", "copytrade_lead")
ENVIRONMENTS = ("paper", "testnet", "demo", "live")
MARKET_DATA_SOURCES = ("live", "exchange")

# Which environments each mode supports.
#   spot:    paper, testnet (testnet.binance.vision), live
#   futures: demo (Binance Demo Trading, demo-fapi.binance.com), live
MODE_ENVIRONMENTS = {
    "spot": ("paper", "testnet", "live"),
    "futures": ("demo", "live"),
}

# Leverage can never be set above this, whatever the config says.
MAX_LEVERAGE_HARD_CAP = 3

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
class FuturesConfig:
    leverage: int = 2
    margin_type: str = "ISOLATED"


@dataclass(frozen=True)
class Credentials:
    """API credentials for one Binance account.

    Either api_secret (HMAC keys, Binance generated the secret) or private_key
    (Ed25519/RSA keys you generated yourself, as PEM text) is set.
    """

    api_key: str
    api_secret: str | None = None
    private_key: str | None = None
    private_key_passphrase: str | None = None

    @property
    def secret_values(self) -> list[str]:
        """Every sensitive string, so the logger can mask them."""
        values = [self.api_key, self.api_secret, self.private_key_passphrase]
        if self.private_key:
            values += [line for line in self.private_key.splitlines() if "-----" not in line]
        return [v for v in values if v]

    def __repr__(self) -> str:
        # Never show real keys, even when someone prints this object while debugging.
        kind = "private_key" if self.private_key else "api_secret"
        return f"Credentials(api_key=***, {kind}=***)"


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
    level: int = 0
    futures: FuturesConfig = field(default_factory=FuturesConfig)
    market_data: str = "live"
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
        fut_raw = raw.get("futures", {}) or {}
        futures = FuturesConfig(
            leverage=int(fut_raw.get("leverage", 2)),
            margin_type=str(fut_raw.get("margin_type", "ISOLATED")).upper(),
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
            level=raw.get("level", 0),
            futures=futures,
            market_data=str(raw.get("market_data", "live")),
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
    allowed = MODE_ENVIRONMENTS.get(cfg.mode)
    if allowed and cfg.environment not in allowed:
        raise ConfigError(f"mode {cfg.mode!r} supports environments {allowed}, got {cfg.environment!r}")
    if cfg.market_data not in MARKET_DATA_SOURCES:
        raise ConfigError(f"market_data must be one of {MARKET_DATA_SOURCES}, got {cfg.market_data!r}")
    if not cfg.pairs:
        raise ConfigError("pairs must list at least one symbol")
    if risk.max_order_value <= 0 or risk.max_open_positions <= 0 or risk.max_daily_loss <= 0:
        raise ConfigError("risk limits must all be positive numbers")
    if not 1 <= futures.leverage <= MAX_LEVERAGE_HARD_CAP:
        raise ConfigError(f"futures.leverage must be 1..{MAX_LEVERAGE_HARD_CAP} (hard cap), got {futures.leverage}")
    if futures.margin_type != "ISOLATED":
        raise ConfigError("futures.margin_type must be ISOLATED (cross margin is not allowed in this bot)")
    level_problem = check_level_config(cfg.level, cfg.environment)
    if level_problem:
        raise ConfigError(level_problem)
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


# ---- credentials -------------------------------------------------------------

_PEM_BLOCK = re.compile(r"-----BEGIN ((?:ENCRYPTED )?PRIVATE KEY)-----(.*?)-----END \1-----", re.S)


def normalize_private_key(text: str) -> str:
    """Turn whatever the user stored into a clean PEM private key.

    Accepts: a full PEM file (also with the public key in the same file, Windows
    line endings, extra spaces) or just the one-line key body. Returns only the
    private-key block with '\\n' line endings. Clean input matters: CCXT decides
    Ed25519 vs RSA by length, so stray characters break signing.
    """
    text = text.replace("\r", "").strip()
    match = _PEM_BLOCK.search(text)
    if match:
        label, body = match.group(1), "".join(match.group(2).split())
    else:
        if "-----" in text:
            raise ValueError("Key text has PEM markers but no 'PRIVATE KEY' block (is this the public key?)")
        label, body = "PRIVATE KEY", "".join(text.split())
    if not body or not re.fullmatch(r"[A-Za-z0-9+/=]+", body):
        raise ValueError("Private key body is empty or not base64")
    lines = [body[i:i + 64] for i in range(0, len(body), 64)]
    return f"-----BEGIN {label}-----\n" + "\n".join(lines) + f"\n-----END {label}-----\n"


def load_credentials(account: str, environment: str, env: Mapping[str, str] | None = None) -> Credentials | None:
    """Read API credentials for one account from the environment (.env).

    Names: BINANCE_<ACCOUNT>_<ENVIRONMENT>_ + one of
      API_KEY + API_SECRET            (HMAC key)
      API_KEY + PRIVATE_KEY           (Ed25519/RSA, key text or one-line body in .env)
      API_KEY + PRIVATE_KEY_PATH      (Ed25519/RSA, key stored in a file - use for live)
      optional PRIVATE_KEY_PASSPHRASE (if the key file is encrypted)

    Testnet/demo and live keys use DIFFERENT names, so a test run can never
    accidentally pick up live keys. Returns None if no key is set.
    """
    env = os.environ if env is None else env
    prefix = f"BINANCE_{account.upper()}_{environment.upper()}"

    def get(name: str) -> str:
        return (env.get(f"{prefix}_{name}") or "").strip()

    api_key = get("API_KEY")
    if not api_key:
        return None
    if get("API_SECRET"):
        return Credentials(api_key=api_key, api_secret=get("API_SECRET"))
    key_text = get("PRIVATE_KEY")
    if not key_text and get("PRIVATE_KEY_PATH"):
        key_text = Path(get("PRIVATE_KEY_PATH")).read_text(encoding="utf-8")
    if not key_text:
        return None
    return Credentials(
        api_key=api_key,
        private_key=normalize_private_key(key_text),
        private_key_passphrase=get("PRIVATE_KEY_PASSPHRASE") or None,
    )


def credential_env_names(account: str, environment: str) -> tuple[str, str]:
    """The variable names we look for - handy for helpful error messages."""
    prefix = f"BINANCE_{account.upper()}_{environment.upper()}"
    return f"{prefix}_API_KEY", f"{prefix}_API_SECRET or {prefix}_PRIVATE_KEY(_PATH)"
