"""Spot mode.

Session 1 does a read-only startup check: connect, print candles + indicators,
print balances. No orders are placed. The trading loop (candle close -> strategy
-> risk -> execution) comes next.

Two clients:
- data_client  - where candles come from (real market data by default)
- trade_client - where balances and orders live (paper / testnet / live)
"""

from __future__ import annotations

import logging

import pandas as pd

from cryptobot import indicators
from cryptobot.config import Config, credential_env_names, load_credentials
from cryptobot.data.candles import get_closed_candles
from cryptobot.exchange.base import ExchangeClient
from cryptobot.exchange.ccxt_client import CcxtSpotClient
from cryptobot.exchange.market_data import BinancePublicData
from cryptobot.exchange.paper_broker import PaperExchangeClient
from cryptobot.notifications import TelegramNotifier
from cryptobot.risk import RiskManager
from cryptobot.storage import TradeStore

log = logging.getLogger(__name__)


def build_clients(cfg: Config) -> tuple[ExchangeClient, ExchangeClient]:
    """Return (data_client, trade_client)."""
    if cfg.environment == "paper":
        paper = PaperExchangeClient(cfg.paper_balances)
        return paper, paper

    creds = load_credentials(cfg.account, cfg.environment)
    if creds is None:
        key_name, secret_name = credential_env_names(cfg.account, cfg.environment)
        log.warning("No API keys found (%s / %s in .env) - market data only, no balances", key_name, secret_name)
    trade_client = CcxtSpotClient(cfg.environment, creds)
    data_client = BinancePublicData() if cfg.market_data == "live" else trade_client
    return data_client, trade_client


def run(cfg: Config) -> None:
    data_client, trade_client = build_clients(cfg)
    data_label = "real market" if cfg.market_data == "live" or cfg.environment == "paper" else cfg.environment
    notifier = TelegramNotifier(cfg.telegram_enabled)

    # Wire the risk manager to SQLite so a halt survives restarts.
    store = TradeStore(cfg.sqlite_path, cfg.environment, cfg.mode, cfg.account)
    risk = RiskManager(cfg.risk, halted_on=store.last_halt_date(),
                       on_halt=lambda reason: (store.record_halt(reason), notifier.send(f"HALT: {reason}")))
    log.info("Risk manager ready (halted today: %s)", risk.is_halted)

    # 1) Candles + a few indicators
    with pd.option_context("display.max_rows", cfg.candle_limit + 10, "display.width", 140):
        for symbol in cfg.pairs:
            candles = get_closed_candles(data_client, symbol, cfg.timeframe, cfg.candle_limit)
            candles["ema20"] = indicators.ema(candles, 20)
            candles["ema50"] = indicators.ema(candles, 50)
            candles["rsi14"] = indicators.rsi(candles, 14)
            candles["atr14"] = indicators.atr(candles, 14)
            print(f"\n=== {symbol} - last {len(candles)} closed {cfg.timeframe} candles ({data_label} data) ===")
            print(candles.round(2))

    # 2) Balances
    print(f"\n=== Balances ({cfg.environment}) ===")
    try:
        balances = trade_client.fetch_balances()
    except RuntimeError as e:
        print(f"Skipped: {e}")
    else:
        for b in sorted(balances.values(), key=lambda b: b.asset):
            print(f"{b.asset:>10}  total={b.total:<18.8f} free={b.free:<18.8f} in orders={b.used:.8f}")
        if not balances:
            print("(no non-zero balances)")

    store.close()
    notifier.send(f"Startup check finished ({cfg.mode}/{cfg.environment})")
