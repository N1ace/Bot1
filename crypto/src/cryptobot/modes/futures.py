"""USDⓈ-M futures mode.

Current step (Prompt A in binance-futures-bot-reference.md), places NO orders:
1. Startup routine: check one-way + single-asset mode, set ISOLATED margin and
   leverage per symbol, load exchange rules (tick/step/min notional).
2. Reconciliation snapshot: balances, open positions, open (algo) orders.
3. Run the market stream (klines + mark price) and the user data stream
   for N minutes, then print a summary.
"""

from __future__ import annotations

import asyncio
import logging

from cryptobot.config import Config, credential_env_names, load_credentials
from cryptobot.data.market_stream import MarketStream
from cryptobot.data.user_stream import UserStream
from cryptobot.exchange.futures_client import FuturesClient, FuturesSetupError
from cryptobot.storage import TradeStore

log = logging.getLogger(__name__)


def run(cfg: Config, minutes: float) -> int:
    creds = load_credentials(cfg.account, cfg.environment)
    if creds is None:
        key_name, secret_name = credential_env_names(cfg.account, cfg.environment)
        log.error("No API keys for futures %s: set %s and %s in .env", cfg.environment, key_name, secret_name)
        return 1

    client = FuturesClient(cfg.environment, creds)
    store = TradeStore(cfg.sqlite_path, cfg.environment, cfg.mode, cfg.account)
    try:
        startup(cfg, client)
    except FuturesSetupError as e:
        log.error("Startup check failed - not trading: %s", e)
        store.close()
        return 1

    print(f"\nRunning streams for {minutes:g} minutes (Ctrl+C to stop early)...")
    market, user = asyncio.run(_run_streams(cfg, client, store, minutes))
    _print_summary(market, user, store)
    store.close()
    return 0


def startup(cfg: Config, client: FuturesClient) -> None:
    print(f"\n=== Futures startup ({cfg.environment}) ===")
    for s in client.startup(cfg.pairs, cfg.futures.leverage):
        print(f"{s.symbol}: margin {s.margin_type}, leverage {s.leverage}x, "
              f"max position size at this leverage {s.max_notional_at_leverage:,} USDT")

    print("\n=== Exchange rules ===")
    for symbol, r in client.rules.items():
        print(f"{symbol}: tick {r.tick_size}  step {r.step_size} (market {r.market_step_size})  "
              f"min qty {r.min_qty}  min order value {r.min_notional} USDT")

    print("\n=== Account (reconciliation snapshot) ===")
    for b in client.balances().values():
        print(f"{b.asset:>6}  balance {_num(b.balance)}  available {_num(b.available)}  "
              f"unrealized PnL {_num(b.unrealized_pnl)}")
    positions = client.positions()
    for p in positions:
        side = "LONG" if p.amount > 0 else "SHORT"
        print(f"{p.symbol} {side} {abs(p.amount)} @ {p.entry_price}  mark {p.mark_price}  "
              f"liq {p.liquidation_price}  uPnL {p.unrealized_pnl}  {p.margin_type} {p.leverage}x")
    normal, algo = client.open_order_counts()
    print(f"open positions: {len(positions)}   open orders: {normal}   open algo (stop) orders: {algo}")


async def _run_streams(cfg: Config, client: FuturesClient, store: TradeStore, minutes: float):
    ws_base = "wss://fstream.binance.com" if cfg.market_data == "live" else client.ws_url
    market = MarketStream(ws_base, cfg.pairs, cfg.timeframe)
    user = UserStream(client, store)
    stop = asyncio.Event()
    tasks = [asyncio.create_task(market.run(stop)), asyncio.create_task(user.run(stop))]
    try:
        await asyncio.wait_for(stop.wait(), timeout=minutes * 60)
    except asyncio.TimeoutError:
        pass
    except asyncio.CancelledError:  # Ctrl+C
        pass
    finally:
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    return market, user


def _print_summary(market: MarketStream, user: UserStream, store: TradeStore) -> None:
    st = market.stats
    print("\n=== Stream summary ===")
    print(f"market stream: {st.messages} messages, {st.closed_candles} closed candles, {st.reconnects} reconnects")
    for m in st.last_mark.values():
        print(f"  {m.symbol}: mark {m.mark_price}  funding {m.funding_rate * 100:.4f}%  "
              f"next funding {m.next_funding_time:%Y-%m-%d %H:%M} UTC")
    print(f"user stream: {sum(user.counts.values())} events {dict(user.counts)}, "
          f"{user.reconnects} reconnects, {user.keepalives} keepalives")
    print(f"stored user events in SQLite (all runs): {store.count_stream_events() or 'none'}")


def _num(value) -> str:
    """Decimal for humans: 5000.00000000 -> '5000', 0E-8 -> '0', 0.02000000 -> '0.02'."""
    text = f"{value.normalize():f}"
    return text if text != "-0" else "0"
