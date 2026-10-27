"""Spot mode.

Session 1 does a read-only startup check: connect, print candles, print balances.
No orders are placed. The trading loop (candle close -> strategy -> risk ->
execution) comes next.
"""

from __future__ import annotations

import logging

import pandas as pd

from cryptobot.config import Config, credential_env_names, load_credentials
from cryptobot.data.candles import get_closed_candles
from cryptobot.exchange.base import ExchangeClient
from cryptobot.exchange.ccxt_client import CcxtSpotClient
from cryptobot.exchange.paper_broker import PaperExchangeClient
from cryptobot.notifications import TelegramNotifier
from cryptobot.risk import RiskManager
from cryptobot.storage import TradeStore

log = logging.getLogger(__name__)


def build_client(cfg: Config) -> ExchangeClient:
    if cfg.environment == "paper":
        return PaperExchangeClient(cfg.paper_balances)
    creds = load_credentials(cfg.account, cfg.environment)
    if creds is None:
        key_name, secret_name = credential_env_names(cfg.account, cfg.environment)
        log.warning("No API keys found (%s / %s in .env) - market data only, no balances", key_name, secret_name)
    return CcxtSpotClient(cfg.environment, creds)


def run(cfg: Config) -> None:
    client = build_client(cfg)
    notifier = TelegramNotifier(cfg.telegram_enabled)

    # Wire the risk manager to SQLite so a halt survives restarts.
    store = TradeStore(cfg.sqlite_path, cfg.environment, cfg.mode, cfg.account)
    risk = RiskManager(cfg.risk, halted_on=store.last_halt_date(),
                       on_halt=lambda reason: (store.record_halt(reason), notifier.send(f"HALT: {reason}")))
    log.info("Risk manager ready (halted today: %s)", risk.is_halted)

    # 1) Candles
    with pd.option_context("display.max_rows", cfg.candle_limit + 10, "display.width", 120):
        for symbol in cfg.pairs:
            candles = get_closed_candles(client, symbol, cfg.timeframe, cfg.candle_limit)
            print(f"\n=== {symbol} - last {len(candles)} closed {cfg.timeframe} candles ({cfg.environment}) ===")
            print(candles)

    # 2) Balances
    print(f"\n=== Balances ({cfg.environment}) ===")
    try:
        balances = client.fetch_balances()
    except RuntimeError as e:
        print(f"Skipped: {e}")
    else:
        for b in sorted(balances.values(), key=lambda b: b.asset):
            print(f"{b.asset:>10}  total={b.total:<18.8f} free={b.free:<18.8f} in orders={b.used:.8f}")
        if not balances:
            print("(no non-zero balances)")

    store.close()
    notifier.send(f"Startup check finished ({cfg.mode}/{cfg.environment})")
