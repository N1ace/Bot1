"""DEMO ONLY: one tiny BTCUSDT test trade to prove the order plumbing works.

    python scripts/demo_test_trade.py

Steps (Prompt B in binance-futures-bot-reference.md):
 1. Safety checks: config must be futures + demo; no existing BTCUSDT position or
    open orders; user data stream connected (so no event is missed).
 2. Entry: minimum-size BTCUSDT LONG at market (~56 USDT, ~28 USDT margin at 2x),
    approved by the RiskManager, saved to SQLite before sending.
 3. Stop-loss: STOP_MARKET via the Algo Order API, closePosition=true,
    workingType=MARK_PRICE, 2% below the entry price.
    Confirmed by BOTH the ALGO_UPDATE event and the open-algo-orders list,
    and checked to sit above the liquidation price. Any failure -> close at once.
 4. Hold 30 seconds.
 5. Close the position (reduce-only market sell), THEN remove the stop - so the
    position is never unprotected (Prompt B said cancel-then-close; the risk
    review recommended this safer order).
 6. Verify: no position, no open stop orders. Print every fill of this run,
    fees and realized PnL.

Whatever goes wrong after the entry, the `finally` block flattens the position
(close first, then cancel stops, checked twice).
Expected cost: two taker fees (~0.05% each of ~56 USDT, about 0.06 USDT in total)
plus the price change over 30 seconds. Demo money only.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from dotenv import load_dotenv

from cryptobot.config import load_config, load_credentials
from cryptobot.data.user_stream import UserStream
from cryptobot.exchange.futures_client import FuturesClient
from cryptobot.logging_setup import setup_logging
from cryptobot.models import OrderRequest, PortfolioSnapshot, Position, Side
from cryptobot.risk import RiskManager
from cryptobot.storage import TradeStore

SYMBOL = "BTCUSDT"
STOP_DISTANCE = Decimal("0.02")   # stop 2% below entry
HOLD_SECONDS = 30
EVENT_TIMEOUT_S = 15

log = logging.getLogger("demo_test_trade")


class Abort(Exception):
    """Stop the test; the finally block makes sure no position is left open."""


def new_client_id(tag: str) -> str:
    # Binance allows up to 36 chars of [.A-Z:/a-z0-9_-]; unique so a retry can't duplicate.
    return f"cbtest-{tag}-{uuid.uuid4().hex[:16]}"


class EventLog:
    """Collects user-stream events so the script can wait for a specific one."""

    def __init__(self):
        self.events: list[dict] = []

    def add(self, event: dict) -> None:
        self.events.append(event)

    async def wait_for(self, predicate, timeout: float = EVENT_TIMEOUT_S) -> dict | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for e in self.events:
                if predicate(e):
                    return e
            await asyncio.sleep(0.2)
        return None


def algo_event(client_algo_id: str, status: str):
    return lambda e: (e.get("e") == "ALGO_UPDATE" and e.get("o", {}).get("caid") == client_algo_id
                      and e.get("o", {}).get("X") == status)


def order_filled_event(client_order_id: str):
    return lambda e: (e.get("e") == "ORDER_TRADE_UPDATE" and e.get("o", {}).get("c") == client_order_id
                      and e.get("o", {}).get("X") == "FILLED")


def snapshot(client: FuturesClient) -> PortfolioSnapshot:
    usdt = client.balances().get("USDT")
    positions = {
        p.symbol: Position(p.symbol, Side.BUY if p.amount > 0 else Side.SELL, float(abs(p.amount)),
                           float(p.entry_price))
        for p in client.positions()
    }
    return PortfolioSnapshot(quote_balance=float(usdt.available) if usdt else 0.0, open_positions=positions)


def approve(risk: RiskManager, client: FuturesClient, request: OrderRequest) -> None:
    decision = risk.review(request, snapshot(client))
    if not decision.approved:
        raise Abort(f"RiskManager rejected {request.side.value} {request.symbol}: {decision.reasons}")


async def run_test(client: FuturesClient, store: TradeStore, risk: RiskManager, events: EventLog) -> None:
    rules = client.rules[SYMBOL]
    mark = await asyncio.to_thread(client.mark_price, SYMBOL)
    # Size for the minimum order value at a 2% lower price, so a small move can't push it below the minimum.
    qty = rules.min_qty_for_notional(mark * (1 - STOP_DISTANCE))
    planned_stop = rules.round_price(mark * (1 - STOP_DISTANCE), "up")
    print(f"\nMark price {mark}. Entry: BUY {qty} {SYMBOL} at market (~{(qty * mark):.2f} USDT), "
          f"stop ~{planned_stop}")

    # --- 2. entry ---------------------------------------------------------------
    entry_req = OrderRequest(SYMBOL, Side.BUY, float(qty), float(mark), stop_loss=float(planned_stop),
                             order_type="MARKET", strategy="demo_test_trade")
    approve(risk, client, entry_req)
    entry_id = new_client_id("entry")
    store.record_order(entry_req, "requested", client_order_id=entry_id)   # saved BEFORE sending
    entry = await asyncio.to_thread(client.place_market_order, SYMBOL, "BUY", qty, entry_id)
    store.update_order(entry_id, entry["status"], str(entry["orderId"]))
    if entry["status"] != "FILLED":
        raise Abort(f"Entry not filled: status {entry['status']}")
    entry_price, filled_qty = Decimal(entry["avgPrice"]), Decimal(entry["executedQty"])
    print(f"ENTRY FILLED: {filled_qty} @ {entry_price}")

    # --- 3. stop-loss -------------------------------------------------------------
    stop_price = rules.round_price(entry_price * (1 - STOP_DISTANCE), "up")
    stop_req = OrderRequest(SYMBOL, Side.SELL, float(filled_qty), float(stop_price),
                            order_type="STOP_MARKET_ALGO", is_exit=True, strategy="demo_test_trade")
    approve(risk, client, stop_req)
    stop_id = new_client_id("stop")
    store.record_order(stop_req, "requested", client_order_id=stop_id)
    try:
        algo = await asyncio.to_thread(client.place_stop_market_close, SYMBOL, "SELL", stop_price, stop_id)
    except Exception as e:
        store.update_order(stop_id, "failed", note=str(e))
        raise Abort(f"Placing the stop-loss FAILED ({e}) - closing the position") from e
    store.update_order(stop_id, str(algo.get("algoStatus", "NEW")), str(algo.get("algoId")))

    confirmed_by_event = await events.wait_for(algo_event(stop_id, "NEW"))
    open_algos = await asyncio.to_thread(client.open_algo_orders, SYMBOL)
    in_open_list = any(a.get("clientAlgoId") == stop_id for a in open_algos)
    print(f"STOP PLACED: trigger {stop_price} (mark price), closePosition. "
          f"ALGO_UPDATE NEW: {'yes' if confirmed_by_event else 'NO'}; in open algo orders: "
          f"{'yes' if in_open_list else 'NO'}")
    if not (confirmed_by_event and in_open_list):
        raise Abort("Stop-loss could not be confirmed - closing the position")

    position = next(p for p in await asyncio.to_thread(client.positions) if p.symbol == SYMBOL)
    print(f"POSITION: {position.amount} @ {position.entry_price}, liquidation {position.liquidation_price}, "
          f"{position.margin_type} {position.leverage}x")
    if position.liquidation_price >= stop_price:
        raise Abort(f"Stop {stop_price} is not above liquidation {position.liquidation_price} - closing")

    # --- 4. hold --------------------------------------------------------------------
    for remaining in range(HOLD_SECONDS, 0, -10):
        print(f"  holding... {remaining}s left, mark {await asyncio.to_thread(client.mark_price, SYMBOL)}")
        await asyncio.sleep(min(10, remaining))

    # --- 5. close, then remove the stop --------------------------------------------------
    # Close FIRST, so the position is never without a stop. A closePosition stop can
    # only reduce a position, so once flat it can't open anything.
    close_req = OrderRequest(SYMBOL, Side.SELL, float(filled_qty), float(await asyncio.to_thread(
        client.mark_price, SYMBOL)), order_type="MARKET", is_exit=True, strategy="demo_test_trade")
    approve(risk, client, close_req)
    close_id = new_client_id("close")
    store.record_order(close_req, "requested", client_order_id=close_id)
    close = await asyncio.to_thread(client.place_market_order, SYMBOL, "SELL", filled_qty, close_id, True)
    store.update_order(close_id, close["status"], str(close["orderId"]))
    print(f"CLOSE: {close['status']} {close['executedQty']} @ {close['avgPrice']}")
    await events.wait_for(order_filled_event(close_id))

    if any(a.get("clientAlgoId") == stop_id for a in await asyncio.to_thread(client.open_algo_orders, SYMBOL)):
        await asyncio.to_thread(client.cancel_algo_order, stop_id)
    gone = await events.wait_for(lambda e: algo_event(stop_id, "CANCELED")(e) or algo_event(stop_id, "EXPIRED")(e))
    status = gone["o"]["X"] if gone else "unconfirmed"
    store.update_order(stop_id, status)
    print(f"STOP REMOVED (ALGO_UPDATE: {status})")

    # --- 6. verify + summary --------------------------------------------------------
    await asyncio.sleep(1)  # let the last fill events arrive
    left = [p for p in await asyncio.to_thread(client.positions) if p.symbol == SYMBOL]
    algos_left = await asyncio.to_thread(client.open_algo_orders, SYMBOL)
    print(f"\nVERIFY: open {SYMBOL} positions {len(left)}, open stop orders {len(algos_left)}")
    if left or algos_left:
        raise Abort("Something is still open after closing - see above")


def print_fills(store: TradeStore, since_iso: str) -> None:
    """Every BTCUSDT fill since the script started (entry, close, stop or emergency fills)."""
    fills = store.fills_since(SYMBOL, since_iso)
    fees = sum(f["fee"] for f in fills)
    pnl = sum(f["realized_pnl"] for f in fills)
    print("\n=== Fills this run (from user data stream, saved in SQLite) ===")
    for f in fills:
        print(f"  {f['timestamp']}  {f['side']:<4} {f['quantity']} @ {f['price']}  fee {f['fee']} {f['fee_asset']}"
              f"  realized PnL {f['realized_pnl']}")
    if not fills:
        print("  (none recorded)")
    print(f"realized PnL {pnl:.4f} USDT, fees {fees:.4f} USDT, net {pnl - fees:.4f} USDT")


async def flatten(client: FuturesClient, store: TradeStore) -> None:
    """Emergency cleanup: cancel stop orders and close any BTCUSDT position at market.

    Deliberately skips RiskManager.review(): it always approves exits anyway, and a
    bug there must never be able to block getting OUT of a position.

    Order matters: close the position FIRST, then remove stops. Each step has its own
    error handling so one failure can't skip the others. The position check runs
    twice (now and again after a few seconds), in case an order that was still in
    flight fills late.
    """
    failures = []
    for check in (1, 2):
        try:
            for p in await asyncio.to_thread(client.positions):
                if p.symbol == SYMBOL and p.amount != 0:
                    side = "SELL" if p.amount > 0 else "BUY"
                    close_id = new_client_id("flat")
                    store.record_order(OrderRequest(SYMBOL, Side(side.lower()), float(abs(p.amount)),
                                                    float(p.mark_price), is_exit=True, strategy="demo_test_trade"),
                                       "requested", client_order_id=close_id, note="emergency close")
                    done = await asyncio.to_thread(client.place_market_order, SYMBOL, side, abs(p.amount),
                                                   close_id, True)
                    store.update_order(close_id, done["status"], str(done["orderId"]))
                    log.warning("Cleanup: closed position %s %s -> %s", side, abs(p.amount), done["status"])
        except Exception as e:
            failures.append(f"close position (check {check}): {e}")
            log.exception("Cleanup: closing the position failed")
        try:
            for algo in await asyncio.to_thread(client.open_algo_orders, SYMBOL):
                if str(algo.get("clientAlgoId", "")).startswith("cbtest-"):
                    await asyncio.to_thread(client.cancel_algo_order, algo["clientAlgoId"])
                    log.warning("Cleanup: canceled stop %s", algo["clientAlgoId"])
        except Exception as e:
            failures.append(f"cancel stops (check {check}): {e}")
            log.exception("Cleanup: canceling stop orders failed")
        if check == 1:
            await asyncio.sleep(5)
    if failures:
        raise RuntimeError("CLEANUP FAILED - check the demo account manually (Futures > Positions / "
                           "Open Orders): " + "; ".join(failures))


async def main() -> int:
    load_dotenv()
    cfg = load_config("config/config.yaml")
    # Hard stop: this script must never touch anything but the demo account.
    if cfg.mode != "futures" or cfg.environment != "demo" or cfg.live_trading:
        print("Refusing: config must be mode=futures, environment=demo, live_trading=false", file=sys.stderr)
        return 2
    creds = load_credentials(cfg.account, "demo")
    if creds is None:
        print("No futures demo keys in .env", file=sys.stderr)
        return 2
    setup_logging(cfg.log_level, cfg.log_dir, secrets=creds.secret_values)

    client = FuturesClient("demo", creds)
    store = TradeStore(cfg.sqlite_path, "demo", "futures", cfg.account)
    risk = RiskManager(cfg.risk, halted_on=store.last_halt_date(), on_halt=store.record_halt)

    client.startup([SYMBOL], cfg.futures.leverage)
    if any(p.symbol == SYMBOL for p in client.positions()) or client.open_algo_orders(SYMBOL):
        print(f"Refusing: {SYMBOL} already has a position or open stop orders on demo", file=sys.stderr)
        store.close()
        return 2

    events = EventLog()
    stream = UserStream(client, store, on_event=events.add)
    stop = asyncio.Event()
    stream_task = asyncio.create_task(stream.run(stop))
    try:
        await asyncio.wait_for(stream.connected.wait(), 20)
    except asyncio.TimeoutError:
        stop.set()
        await stream_task
        print("User data stream did not connect - not trading", file=sys.stderr)
        return 1

    code = 0
    started = datetime.now(timezone.utc).isoformat()
    try:
        await run_test(client, store, risk, events)
        print("\nTEST TRADE COMPLETED OK")
    except Abort as e:
        log.error("ABORTED: %s", e)
        code = 1
    except Exception:
        log.exception("Unexpected error")
        code = 1
    finally:
        try:
            await flatten(client, store)   # no-op when everything is already closed
        except Exception:
            log.exception("Cleanup did not finish")
            code = 1
        finally:
            print_fills(store, started)
            stop.set()
            await stream_task
            store.close()
    return code


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(main()))
