"""The live-trading guard, futures limits and credential loading."""

import dataclasses

import pytest

from cryptobot.config import (LIVE_CONFIRM_ENV, LIVE_CONFIRM_VALUE, ConfigError, _build, check_live_guard,
                              load_config, load_credentials, normalize_private_key)

BASE = {
    "mode": "spot",
    "environment": "testnet",
    "pairs": ["BTC/USDT"],
    "risk": {"max_order_value": 50, "max_open_positions": 2, "max_daily_loss": 30},
}
FUT = {**BASE, "mode": "futures", "environment": "demo", "pairs": ["BTCUSDT"]}

# A syntactically valid Ed25519 PKCS#8 body (random bytes, not a real key).
BODY = "MC4CAQAwBQYDK2VwBCIEIGHHnU3pQK0o9mXWmFRXr0cXbyU0jYdRZ6U0Kbn8fM2D"


def cfg(**overrides):
    return _build({**BASE, **overrides})


# ---- live guard --------------------------------------------------------------

def test_repo_config_is_safe_by_default():
    c = load_config("config/config.yaml", env={})
    assert c.environment in ("paper", "testnet", "demo")
    assert c.live_trading is False


def live_cfg(live_trading: bool):
    """A live config built directly (loading one is refused anyway: level 3 isn't built yet)."""
    return dataclasses.replace(cfg(), environment="live", live_trading=live_trading, level=3)


def test_live_config_cannot_load_while_live_levels_are_not_built():
    with pytest.raises(ConfigError):
        cfg(environment="live", live_trading=True, level=3)


def test_live_requires_config_flag():
    with pytest.raises(ConfigError):
        check_live_guard(live_cfg(False), {LIVE_CONFIRM_ENV: LIVE_CONFIRM_VALUE})


def test_live_requires_env_confirmation():
    with pytest.raises(ConfigError):
        check_live_guard(live_cfg(True), {})
    with pytest.raises(ConfigError):
        check_live_guard(live_cfg(True), {LIVE_CONFIRM_ENV: "yes"})


def test_live_allowed_only_with_both():
    check_live_guard(live_cfg(True), {LIVE_CONFIRM_ENV: LIVE_CONFIRM_VALUE})


def test_live_flag_with_testnet_is_rejected():
    with pytest.raises(ConfigError):
        check_live_guard(cfg(live_trading=True), {})


def test_string_true_does_not_enable_live():
    assert cfg(live_trading="true").live_trading is False


# ---- futures settings --------------------------------------------------------

def test_futures_defaults():
    c = _build(FUT)
    assert c.futures.leverage == 2 and c.futures.margin_type == "ISOLATED"


@pytest.mark.parametrize("leverage", [0, 4, 10, 125])
def test_leverage_hard_cap(leverage):
    with pytest.raises(ConfigError):
        _build({**FUT, "futures": {"leverage": leverage}})


def test_cross_margin_rejected():
    with pytest.raises(ConfigError):
        _build({**FUT, "futures": {"margin_type": "CROSSED"}})


def test_mode_environment_combinations():
    with pytest.raises(ConfigError):
        _build({**FUT, "environment": "testnet"})   # futures testnet was replaced by demo
    with pytest.raises(ConfigError):
        _build({**BASE, "environment": "demo"})      # spot has no demo env in this bot


# ---- levels ------------------------------------------------------------------

def test_level_defaults_to_observer():
    assert _build(FUT).level == 0


@pytest.mark.parametrize("level", [3, 4, 5, 6, 7, -1, "2", True])
def test_unavailable_or_invalid_levels_refused(level):
    with pytest.raises(ConfigError):
        _build({**FUT, "level": level})


def test_live_environment_needs_live_level():
    with pytest.raises(ConfigError, match="level 3"):
        _build({**FUT, "environment": "live", "level": 2})


def test_require_level():
    from cryptobot.levels import LevelError, require_level
    require_level(0, "collect_data")
    require_level(0, "report")
    with pytest.raises(LevelError):
        require_level(0, "paper_trade")
    with pytest.raises(LevelError):
        require_level(1, "demo_order")
    require_level(2, "demo_order")


# ---- credentials -------------------------------------------------------------

def test_testnet_never_reads_live_keys():
    env = {"BINANCE_SPOT_LIVE_API_KEY": "live-key-123", "BINANCE_SPOT_LIVE_API_SECRET": "live-secret-123"}
    assert load_credentials("SPOT", "testnet", env) is None
    assert "live-key" not in repr(load_credentials("SPOT", "live", env))


def test_hmac_credentials():
    c = load_credentials("SPOT", "testnet", {"BINANCE_SPOT_TESTNET_API_KEY": "k" * 64,
                                            "BINANCE_SPOT_TESTNET_API_SECRET": "s" * 64})
    assert c.api_secret == "s" * 64 and c.private_key is None


def test_private_key_body_in_env():
    c = load_credentials("FUTURES", "demo", {"BINANCE_FUTURES_DEMO_API_KEY": "k" * 64,
                                             "BINANCE_FUTURES_DEMO_PRIVATE_KEY": BODY})
    assert c.private_key == f"-----BEGIN PRIVATE KEY-----\n{BODY}\n-----END PRIVATE KEY-----\n"
    assert len(c.private_key.strip()) <= 120   # CCXT treats <=120 chars as Ed25519
    assert BODY in c.secret_values
    assert BODY not in repr(c)


def test_private_key_file_with_crlf_and_public_key(tmp_path):
    key_file = tmp_path / "key.txt"
    key_file.write_bytes(
        b"-----BEGIN PUBLIC KEY-----\r\nMCowBQYDK2VwAyEAabc=\r\n-----END PUBLIC KEY-----\r\n"
        + f"  -----BEGIN PRIVATE KEY-----\r\n{BODY}\r\n-----END PRIVATE KEY-----  \r\n".encode()
    )
    c = load_credentials("FUTURES", "live", {"BINANCE_FUTURES_LIVE_API_KEY": "k" * 64,
                                             "BINANCE_FUTURES_LIVE_PRIVATE_KEY_PATH": str(key_file)})
    assert c.private_key == normalize_private_key(BODY)
    assert "PUBLIC" not in c.private_key and "\r" not in c.private_key


def test_public_key_alone_is_rejected():
    with pytest.raises(ValueError, match="public key"):
        normalize_private_key("-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEAabc=\n-----END PUBLIC KEY-----")


def test_encrypted_key_keeps_label():
    pem = normalize_private_key(f"-----BEGIN ENCRYPTED PRIVATE KEY-----\n{BODY}\n-----END ENCRYPTED PRIVATE KEY-----")
    assert pem.startswith("-----BEGIN ENCRYPTED PRIVATE KEY-----")


def test_missing_secret_returns_none():
    assert load_credentials("FUTURES", "demo", {"BINANCE_FUTURES_DEMO_API_KEY": "k" * 64}) is None
