"""The live-trading guard and credential loading."""

import pytest

from cryptobot.config import (LIVE_CONFIRM_ENV, LIVE_CONFIRM_VALUE, ConfigError, _build, check_live_guard,
                              load_config, load_credentials)

BASE = {
    "mode": "spot",
    "environment": "testnet",
    "pairs": ["BTC/USDT"],
    "risk": {"max_order_value": 50, "max_open_positions": 2, "max_daily_loss": 30},
}


def cfg(**overrides):
    return _build({**BASE, **overrides})


def test_repo_config_is_safe_by_default():
    c = load_config("config/config.yaml", env={})
    assert c.environment in ("paper", "testnet")
    assert c.live_trading is False


def test_live_requires_config_flag():
    with pytest.raises(ConfigError):
        check_live_guard(cfg(environment="live"), {LIVE_CONFIRM_ENV: LIVE_CONFIRM_VALUE})


def test_live_requires_env_confirmation():
    with pytest.raises(ConfigError):
        check_live_guard(cfg(environment="live", live_trading=True), {})
    with pytest.raises(ConfigError):
        check_live_guard(cfg(environment="live", live_trading=True), {LIVE_CONFIRM_ENV: "yes"})


def test_live_allowed_only_with_both():
    check_live_guard(cfg(environment="live", live_trading=True), {LIVE_CONFIRM_ENV: LIVE_CONFIRM_VALUE})


def test_live_flag_with_testnet_is_rejected():
    with pytest.raises(ConfigError):
        check_live_guard(cfg(live_trading=True), {})


def test_string_true_does_not_enable_live():
    assert cfg(live_trading="true").live_trading is False


def test_testnet_never_reads_live_keys():
    env = {"BINANCE_SPOT_LIVE_API_KEY": "live-key-123", "BINANCE_SPOT_LIVE_API_SECRET": "live-secret-123"}
    assert load_credentials("SPOT", "testnet", env) is None
    assert "live-key" not in repr(load_credentials("SPOT", "live", env))
