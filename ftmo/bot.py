"""Demo bot: runs noise_area on one MT5 demo account, to collect results on prices the lab has never seen.

noise_area failed its lab test. This is a forward test on a demo, not an approved tactic, and it is not for a paid account.

Dry run by default, which never sends an order. Placing demo orders needs the flag and that account's login number:

    python -m ftmo.bot                                     # dry run
    python -m ftmo.bot --place-demo-orders --login 123456  # owner's decision, run by the owner

It stops if the account isn't a demo. Every decision goes in data/ftmo/journal.jsonl, using the same code as the backtest.
"""

import argparse
import json
import math
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd

from ftmo import backtest
from ftmo.tactics import noise_area as tactic

SYMBOL = "US100.cash"
MAGIC = 26110101  # marks this bot's positions
TARGET_DAILY_VOL = 0.005  # the lab's test level
HISTORY_BARS = 45_000  # enough for the lookback and the volatility window
POLL_SECONDS = 20
MAX_REQUESTS_PER_DAY = 1000  # FTMO's limit is 2,000 server requests
MAX_DEVIATION_POINTS = 50

SESSION_OPEN = 9 * 60 + 30
FOMC_FLAT_FROM = 13 * 60 + 55 - SESSION_OPEN  # flat 5 minutes before the 14:00 statement
FOMC_RESUME = 14 * 60 + 30 - SESSION_OPEN  # the next half-hour check after the press conference starts
LAST_MINUTE = backtest.SESSION_MINUTES - 1  # 15:59: close everything

# Official Federal Reserve calendar, read 2026-11-01. Each is the statement day. 2027 dates are tentative
FOMC_DAYS = {date.fromisoformat(d) for d in (
    "2026-12-09", "2027-01-27", "2027-03-17", "2027-04-28", "2027-06-09", "2027-07-28", "2027-09-15", "2027-10-27",
    "2027-12-08",
)}

STATE_PATH = backtest.DATA_DIR / "bot_state.json"
JOURNAL_PATH = backtest.DATA_DIR / "journal.jsonl"


@dataclass
class State:
    start_balance: float = 0.0
    day: str = ""
    day_start: float = 0.0
    halted_day: str = ""  # the day limit was hit on this server date
    halted_for_good: bool = False  # the total limit was hit
    requests_day: str = ""
    requests: int = 0
    last_check: str = ""


def load_state(path: Path = STATE_PATH) -> State:
    return State(**json.loads(path.read_text(encoding="utf-8"))) if path.exists() else State()


def save_state(state: State, path: Path = STATE_PATH) -> None:
    partial = path.with_suffix(".part")
    partial.write_text(json.dumps(asdict(state)), encoding="utf-8")
    partial.replace(path)


def journal(event: str, **details) -> None:
    row = {"utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event, **details}
    with JOURNAL_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    print(row, flush=True)


def check_account(account, place_orders: bool, login: int | None) -> None:
    """Refuse anything that isn't a demo. Order mode must also name this exact account."""
    if account.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO or "demo" not in account.server.lower():
        raise SystemExit("Refused: this isn't a demo account.")
    if place_orders and login != account.login:
        raise SystemExit("Refused: --login must match the logged-in demo account.")


def session_clock(server_time: datetime) -> tuple[date, int]:
    """New York date and minutes since 09:30, from MT5's server clock."""
    ny = server_time - timedelta(hours=backtest.SERVER_HOURS_AHEAD_OF_NY)
    return ny.date(), ny.hour * 60 + ny.minute - SESSION_OPEN


def in_fomc_break(day: date, minute: int) -> bool:
    return day in FOMC_DAYS and FOMC_FLAT_FROM <= minute < FOMC_RESUME


def lots_for(size: float, equity: float, price: float, step: float, smallest: float, largest: float) -> float:
    """Whole lot steps, rounded down. Zero if the trade would be below the smallest lot."""
    steps = math.floor(size * equity / price / step + 1e-9)
    lots = round(steps * step, 8)
    return 0.0 if lots < smallest else min(lots, largest)


def stop_price(direction: int, entry: float, hard_stop: float, equity: float, day_start: float, lots: float,
               digits: int) -> float:
    """The closer of the tactic's stop and the price where today's loss reaches the bot's limit."""
    hard = entry - direction * hard_stop * entry
    room = (equity - day_start * (1 + backtest.DAY_STOP)) / lots  # 1 lot moves $1 per point on this symbol
    limit = entry - direction * room
    return round(max(hard, limit) if direction > 0 else min(hard, limit), digits)


class Bot:
    def __init__(self, place_orders: bool):
        self.place_orders = place_orders
        self.state = load_state()
        self.info = mt5.symbol_info(SYMBOL)

    def position(self):
        mine = [p for p in mt5.positions_get(symbol=SYMBOL) or () if p.magic == MAGIC]
        return mine[0] if mine else None

    def allow_request(self, day: str) -> bool:
        if self.state.requests_day != day:
            self.state.requests_day, self.state.requests = day, 0
        if self.state.requests >= MAX_REQUESTS_PER_DAY:
            journal("request_limit", count=self.state.requests)
            return False
        self.state.requests += 1
        return True

    def send(self, request: dict, why: str) -> bool:
        """Send once and check the reply. An unclear reply is never resent: the next poll reads the real position."""
        if not self.place_orders:
            journal("dry_run_order", why=why, **request)
            return True
        if not self.allow_request(self.state.day):
            return False
        result = mt5.order_send(request)
        code = None if result is None else result.retcode
        journal("order", why=why, retcode=code, comment=None if result is None else result.comment, **request)
        return code == mt5.TRADE_RETCODE_DONE

    def close(self, position, why: str) -> None:
        tick = mt5.symbol_info_tick(SYMBOL)
        long = position.type == mt5.POSITION_TYPE_BUY
        self.send({
            "action": mt5.TRADE_ACTION_DEAL, "symbol": SYMBOL, "volume": position.volume, "position": position.ticket,
            "type": mt5.ORDER_TYPE_SELL if long else mt5.ORDER_TYPE_BUY, "price": tick.bid if long else tick.ask,
            "deviation": MAX_DEVIATION_POINTS, "magic": MAGIC, "comment": "noise close",
            "type_filling": mt5.ORDER_FILLING_IOC, "type_time": mt5.ORDER_TIME_GTC,
        }, why)

    def open(self, direction: int, size: float, hard_stop: float, equity: float) -> None:
        tick = mt5.symbol_info_tick(SYMBOL)
        price = tick.ask if direction > 0 else tick.bid
        lots = lots_for(size, equity, price, self.info.volume_step, self.info.volume_min, self.info.volume_max)
        if lots == 0:
            journal("too_small", size=size, equity=equity)
            return
        stop = stop_price(direction, price, hard_stop, equity, self.state.day_start, lots, self.info.digits)
        self.send({
            "action": mt5.TRADE_ACTION_DEAL, "symbol": SYMBOL, "volume": lots, "price": price, "sl": stop,
            "type": mt5.ORDER_TYPE_BUY if direction > 0 else mt5.ORDER_TYPE_SELL, "deviation": MAX_DEVIATION_POINTS,
            "magic": MAGIC, "comment": "noise open", "type_filling": mt5.ORDER_FILLING_IOC,
            "type_time": mt5.ORDER_TIME_GTC,
        }, f"enter {direction}")

    def read_days(self, minute: int):
        """Closed bars only. None if the bar just before this check hasn't arrived."""
        rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M1, 1, HISTORY_BARS)
        if rates is None or len(rates) == 0:
            return None
        bars = pd.DataFrame(rates)
        bars["time"] = pd.to_datetime(bars["time"], unit="s")
        bars["spread"] = bars["spread"] * self.info.point
        _, last_minute = session_clock(bars["time"].iloc[-1].to_pydatetime())
        return backtest.build_days(bars, SYMBOL, keep_today=True) if last_minute == minute - 1 else None

    def check(self, minute: int, position) -> None:
        """One half-hour decision, with the backtest's own code."""
        days = self.read_days(minute)
        if days is None:
            journal("skipped_check", minute=minute, reason="latest closed bar is missing")
            return
        levels = tactic.levels(days, tactic.SETTINGS)
        sizes = backtest.position_sizes(days, TARGET_DAILY_VOL,
                                        TARGET_DAILY_VOL / tactic.PAPER_VOL_TARGET * tactic.PAPER_MAX_EXPOSURE)
        today = len(days.dates) - 1
        if not levels.ready[today] or sizes[today] <= 0:
            journal("skipped_check", minute=minute, reason="not enough history")
            return
        current = 0 if position is None else (1 if position.type == mt5.POSITION_TYPE_BUY else -1)
        price = days.close[today, minute - 1]
        target = tactic.decide(today, minute - 1, price, current, levels)
        journal("check", minute=minute, price=price, current=current, target=target, size=float(sizes[today]))
        if target == current:
            return
        if position is not None:
            self.close(position, "tactic exit or reversal")
            if target != 0 and self.place_orders and self.position() is not None:
                journal("entry_skipped", reason="old position still open")
                return
        if target != 0:
            self.open(target, float(sizes[today]), float(levels.hard_stop[today]), mt5.account_info().equity)

    def step(self) -> None:
        tick, account = mt5.symbol_info_tick(SYMBOL), mt5.account_info()
        if tick is None or account is None:
            journal("no_data", error=str(mt5.last_error()))  # stops at the broker still protect an open trade
            return
        server_time = datetime.fromtimestamp(tick.time, timezone.utc).replace(tzinfo=None)
        server_day = server_time.date().isoformat()
        state = self.state
        if state.start_balance == 0:
            state.start_balance = account.balance
        if state.day != server_day:
            state.day, state.day_start = server_day, max(account.balance, account.equity)

        day, minute = session_clock(server_time)
        position = self.position()
        if position is not None and position.sl == 0:
            self.close(position, "no stop on the position")
            position = None

        if account.equity <= state.start_balance * (1 + backtest.TOTAL_STOP):
            state.halted_for_good = True
        if account.equity <= state.day_start * (1 + backtest.DAY_STOP):
            state.halted_day = server_day

        must_close = state.halted_for_good or state.halted_day == server_day or minute >= LAST_MINUTE \
            or in_fomc_break(day, minute)
        if must_close:
            if position is not None:
                self.close(position, "limit, close-out or FOMC break")
        elif minute in tactic.CHECK_MINUTES and state.last_check != f"{day} {minute}":
            state.last_check = f"{day} {minute}"
            self.check(minute, position)
        save_state(state)

    def run(self, once: bool) -> None:
        while True:
            self.step()
            if once:
                return
            time.sleep(POLL_SECONDS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--place-demo-orders", action="store_true")
    parser.add_argument("--login", type=int)
    parser.add_argument("--once", action="store_true", help="run one step, for checking")
    args = parser.parse_args()
    if not mt5.initialize():
        raise SystemExit(f"Can't reach MT5: {mt5.last_error()}")
    try:
        account = mt5.account_info()
        check_account(account, args.place_demo_orders, args.login)
        if not mt5.symbol_select(SYMBOL, True):
            raise SystemExit(f"{SYMBOL} isn't available.")
        foreign = [p for p in mt5.positions_get(symbol=SYMBOL) or () if p.magic != MAGIC]
        if foreign:
            raise SystemExit("Refused: this account has other positions on the symbol.")
        if args.place_demo_orders and not mt5.terminal_info().trade_allowed:
            raise SystemExit("Refused: turn on Algo Trading in MT5 first.")
        journal("started", server=account.server, place_orders=args.place_demo_orders)
        Bot(args.place_demo_orders).run(args.once)
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
