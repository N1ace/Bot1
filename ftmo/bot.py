"""Demo bot: runs noise_area on one MT5 demo account, to collect results on prices the lab has never seen.

noise_area failed its lab test. This is a forward test on a demo, not an approved tactic, and it is not for a paid account.

Dry run by default, which never sends an order. Placing demo orders needs the flag and that account's login number:

    python -m ftmo.bot                                     # dry run
    python -m ftmo.bot --place-demo-orders --login 123456  # owner's decision

A paid challenge can also look like a demo, so the login check is what keeps this bot off one. It's repeated every step.
State and journal live outside OneDrive, one set per login and mode, because file syncing can lock them.
"""

import argparse
import json
import math
import os
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import MetaTrader5 as mt5
import numpy as np
import pandas as pd

from ftmo import backtest
from ftmo.tactics import noise_area as tactic

SYMBOL = "US100.cash"
MAGIC = 26110101  # marks this bot's positions
TARGET_DAILY_VOL = 0.005  # the lab's test level
HISTORY_BARS = 45_000  # enough for the lookback and the volatility window
POLL_SECONDS = 20
MAX_DEVIATION_POINTS = 500  # 5 index points, so a close isn't refused in a fast market
STALE_SECONDS = 120  # no price for this long means the feed, or the clock, can't be trusted
MIN_STOP_SPREADS = 2  # refuse an entry whose stop would sit this close to the price
MAX_SPREAD = 2 * backtest.MEASURED_SPREAD[SYMBOL]  # news or thin markets: skip the entry, exits are never blocked
SPIKE_MINUTES = 5
SPIKE_MULTIPLE = 3.0  # skip an entry if the last 5 minutes ran this many times their usual range
SPIKE_DAYS = 20

# FTMO's limit is 2,000 server requests a day. Entries stop first, so closes always have some left
ENTRY_REQUEST_LIMIT = 800
REQUEST_LIMIT = 1000
CLOSE_RETRY_SECONDS = (20, 40, 80, 160, 300)

NEW_YORK = ZoneInfo("America/New_York")
SESSION_OPEN = 9 * 60 + 30
FOMC_FLAT_FROM = 13 * 60 + 55 - SESSION_OPEN  # flat 5 minutes before the 14:00 statement
FOMC_RESUME = 14 * 60 + 30 - SESSION_OPEN  # the next half-hour check after the press conference starts
LAST_MINUTE = backtest.SESSION_MINUTES - 1  # 15:59: close everything

# Official Federal Reserve calendar, read 2026-11-01. Each is the statement day. 2027 dates are tentative
FOMC_DAYS = {date.fromisoformat(d) for d in (
    "2026-12-09", "2027-01-27", "2027-03-17", "2027-04-28", "2027-06-09", "2027-07-28", "2027-09-15", "2027-10-27",
    "2027-12-08",
)}

# NYSE holidays and 13:00 closes, read 2026-11-01. The backtest dropped such short days, so the bot doesn't trade them.
# Jan 1 and 18, 2027 weren't in the part of the page checked: they follow the fixed holiday rules
NO_TRADE_DAYS = {date.fromisoformat(d) for d in (
    "2026-11-26", "2026-11-27", "2026-12-24", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26",
    "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-11-26", "2027-12-24",
)}

BOT_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ftmo-bot"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class State:
    login: int = 0
    start_balance: float = 0.0
    day: str = ""
    day_start: float = 0.0
    halted_day: str = ""  # the day limit was hit on this server date
    halted_for_good: bool = False  # the total limit was hit
    requests_day: str = ""
    requests: int = 0
    last_check: str = ""
    last_deal: int = 0


def file_stem(login: int, place_orders: bool) -> Path:
    return BOT_DIR / f"{login}_{'demo' if place_orders else 'dry'}"


def load_state(path: Path, login: int) -> State:
    if not path.exists():
        return State(login=login)
    state = State(**json.loads(path.read_text(encoding="utf-8")))
    if state.login != login:
        raise SystemExit(f"Refused: {path} belongs to another account.")
    return state


def save_state(state: State, path: Path) -> None:
    partial = path.with_suffix(".part")
    partial.write_text(json.dumps(asdict(state)), encoding="utf-8")
    partial.replace(path)


def check_account(account, place_orders: bool, login: int | None) -> None:
    """Refuse anything that isn't a demo. Order mode must also name this exact account."""
    if account is None or account.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO or "demo" not in account.server.lower():
        raise SystemExit("Refused: this isn't a demo account.")
    if place_orders and login != account.login:
        raise SystemExit("Refused: --login must match the logged-in demo account.")


def check_symbol(info, account) -> None:
    """Sizing assumes a USD account where 1 lot moves $1 per point."""
    if info is None:
        raise SystemExit(f"{SYMBOL} isn't available.")
    per_point = info.trade_tick_value / info.trade_tick_size if info.trade_tick_size else 0
    if not math.isclose(per_point, 1.0) or info.currency_profit != "USD" or account.currency != "USD":
        raise SystemExit("Refused: expected a USD account with 1 lot worth $1 per point.")


def new_york_clock(utc: datetime) -> tuple[date, int, datetime]:
    """New York date, minutes since 09:30, and what MT5's server clock should read now."""
    ny = utc.astimezone(NEW_YORK).replace(tzinfo=None)
    server = ny + timedelta(hours=backtest.SERVER_HOURS_AHEAD_OF_NY)
    return ny.date(), ny.hour * 60 + ny.minute - SESSION_OPEN, server


def in_fomc_break(day: date, minute: int) -> bool:
    return day in FOMC_DAYS and FOMC_FLAT_FROM <= minute < FOMC_RESUME


def symbol_is_trading(server_now: datetime) -> bool:
    """MT5 Specification hours: Monday to Friday, 01:05 to 23:50 server time."""
    minutes = server_now.hour * 60 + server_now.minute
    return server_now.weekday() < 5 and 65 <= minutes < 23 * 60 + 50


def volatility_spike(days, today: int, minute: int) -> bool:
    """Compare the last few closed minutes with the same minutes on earlier days."""
    start = max(0, minute - SPIKE_MINUTES)
    first = max(0, today - SPIKE_DAYS)
    if today == first:
        return False
    ranges = days.high[:, start:minute].max(axis=1) - days.low[:, start:minute].min(axis=1)
    return bool(ranges[today] > SPIKE_MULTIPLE * np.median(ranges[first:today]))


def lots_for(size: float, equity: float, price: float, step: float, smallest: float, largest: float) -> float:
    """Whole lot steps, rounded down. Zero if the trade would be below the smallest lot."""
    steps = math.floor(size * equity / price / step + 1e-9)
    lots = round(steps * step, 8)
    return 0.0 if lots < smallest else min(lots, largest)


def equity_floor(day_start: float, start_balance: float) -> float:
    """The higher of the two limits: the equity below which a new stop must not allow us to go."""
    return max(day_start * (1 + backtest.DAY_STOP), start_balance * (1 + backtest.TOTAL_STOP))


def stop_price(direction: int, entry: float, hard_stop: float, equity: float, floor: float, lots: float,
               tick_size: float) -> float:
    """The closer of the tactic's stop and the price where equity reaches the floor. Rounded towards the entry."""
    hard = entry - direction * hard_stop * entry
    limit = entry - direction * (equity - floor) / lots  # 1 lot moves $1 per point: checked at startup
    if direction > 0:
        return round(math.ceil(max(hard, limit) / tick_size) * tick_size, 8)
    return round(math.floor(min(hard, limit) / tick_size) * tick_size, 8)


def stop_is_usable(direction: int, stop: float, bid: float, ask: float) -> bool:
    """On the correct side of the current price, with room for more than the spread."""
    room = MIN_STOP_SPREADS * (ask - bid)
    return stop < bid - room if direction > 0 else stop > ask + room


class Bot:
    def __init__(self, place_orders: bool, login: int, stem: Path):
        self.place_orders = place_orders
        self.login = login
        self.state_path = stem.with_suffix(".state.json")
        self.journal_path = stem.with_suffix(".journal.jsonl")
        self.state = load_state(self.state_path, login)
        self.info = mt5.symbol_info(SYMBOL)
        self.close_failures = 0
        self.next_close_at = 0.0
        self.last_alert = ""
        self.pending_exit = False  # a tactic exit that hasn't filled yet

    def journal(self, event: str, **details) -> None:
        row = {"utc": utc_now().isoformat(timespec="seconds"), "event": event, **details}
        try:  # a full or locked disk mustn't stop a close
            print(row, flush=True)
            with self.journal_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, default=str) + "\n")
        except (OSError, ValueError):
            pass

    def alert(self, event: str, **details) -> None:
        """Journal a repeating problem once, until it changes."""
        if event != self.last_alert:
            self.last_alert = event
            self.journal(event, **details)

    def positions(self) -> list | None:
        """This bot's positions. None means the read failed, which is not the same as flat."""
        found = mt5.positions_get(symbol=SYMBOL)
        return None if found is None else [p for p in found if p.magic == MAGIC]

    def allow_request(self, limit: int) -> bool:
        if self.state.requests_day != self.state.day:
            self.state.requests_day, self.state.requests = self.state.day, 0
        if self.state.requests >= limit:
            self.alert("request_limit", count=self.state.requests)
            return False
        self.state.requests += 1
        return True

    def send(self, request: dict, why: str, limit: int) -> bool:
        """Send once and check the reply. An unclear reply is never resent: the next poll reads the real position."""
        account = mt5.account_info()
        if account is None:  # a lost connection, not a wrong account: treat it as a failed send
            return False
        check_account(account, self.place_orders, self.login)
        if not self.place_orders:
            self.journal("dry_run_order", why=why, request=request)
            return True
        if not self.allow_request(limit):
            return False
        self.journal("order_intent", why=why, request=request)
        result = mt5.order_send(request)
        if result is None:
            self.journal("order_reply", why=why, reply=None, error=str(mt5.last_error()))
            return False
        self.journal("order_reply", why=why, reply={k: v for k, v in result._asdict().items() if k != "request"})
        return result.retcode == mt5.TRADE_RETCODE_DONE

    def close(self, position, why: str) -> bool:
        if time.monotonic() < self.next_close_at:
            return False
        tick = mt5.symbol_info_tick(SYMBOL)
        if tick is None:
            return False
        long = position.type == mt5.POSITION_TYPE_BUY
        done = self.send({
            "action": mt5.TRADE_ACTION_DEAL, "symbol": SYMBOL, "volume": position.volume, "position": position.ticket,
            "type": mt5.ORDER_TYPE_SELL if long else mt5.ORDER_TYPE_BUY, "price": tick.bid if long else tick.ask,
            "deviation": MAX_DEVIATION_POINTS, "magic": MAGIC, "comment": "noise close",
            "type_filling": mt5.ORDER_FILLING_IOC, "type_time": mt5.ORDER_TIME_GTC,
        }, why, REQUEST_LIMIT)
        if done:
            self.close_failures, self.next_close_at = 0, 0.0
        else:
            wait = CLOSE_RETRY_SECONDS[min(self.close_failures, len(CLOSE_RETRY_SECONDS) - 1)]
            self.close_failures += 1
            self.next_close_at = time.monotonic() + wait
        return done

    def open(self, direction: int, size: float, hard_stop: float, account) -> None:
        tick = mt5.symbol_info_tick(SYMBOL)
        if tick is None:
            self.journal("entry_skipped", reason="no price")
            return
        if tick.ask - tick.bid > MAX_SPREAD:
            self.journal("entry_skipped", reason="spread too wide", bid=tick.bid, ask=tick.ask)
            return
        price = tick.ask if direction > 0 else tick.bid
        lots = lots_for(size, account.equity, price, self.info.volume_step, self.info.volume_min, self.info.volume_max)
        if lots == 0:
            self.journal("entry_skipped", reason="below the smallest lot", size=size, equity=account.equity)
            return
        floor = equity_floor(self.state.day_start, self.state.start_balance)
        stop = stop_price(direction, price, hard_stop, account.equity, floor, lots, self.info.trade_tick_size)
        if not stop_is_usable(direction, stop, tick.bid, tick.ask):
            self.journal("entry_skipped", reason="stop too close to the price", stop=stop, bid=tick.bid, ask=tick.ask)
            return
        self.send({
            "action": mt5.TRADE_ACTION_DEAL, "symbol": SYMBOL, "volume": lots, "price": price, "sl": stop,
            "type": mt5.ORDER_TYPE_BUY if direction > 0 else mt5.ORDER_TYPE_SELL, "deviation": MAX_DEVIATION_POINTS,
            "magic": MAGIC, "comment": "noise open", "type_filling": mt5.ORDER_FILLING_IOC,
            "type_time": mt5.ORDER_TIME_GTC,
        }, f"enter {direction}", ENTRY_REQUEST_LIMIT)

    def read_days(self, day: date, minute: int):
        """Closed bars only. None if the bar just before this check hasn't arrived yet."""
        rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M1, 1, HISTORY_BARS)
        if rates is None or len(rates) == 0:
            return None
        bars = pd.DataFrame(rates)
        bars["time"] = pd.to_datetime(bars["time"], unit="s")
        bars["spread"] = bars["spread"] * self.info.point
        ny = bars["time"].iloc[-1] - pd.Timedelta(hours=backtest.SERVER_HOURS_AHEAD_OF_NY)
        fresh = ny.date() == day and ny.hour * 60 + ny.minute - SESSION_OPEN == minute - 1
        return backtest.build_days(bars, SYMBOL, keep_today=True) if fresh else None

    def check(self, day: date, minute: int, position, account) -> None:
        """One half-hour decision, with the backtest's own code."""
        days = self.read_days(day, minute)
        if days is None:
            self.alert("check_waiting", minute=minute, reason="latest closed bar is missing")
            return  # try again on the next poll of this minute
        key = f"{day} {minute}"
        levels = tactic.levels(days, tactic.SETTINGS)
        sizes = backtest.position_sizes(days, TARGET_DAILY_VOL,
                                        TARGET_DAILY_VOL / tactic.PAPER_VOL_TARGET * tactic.PAPER_MAX_EXPOSURE)
        today = len(days.dates) - 1
        if not levels.ready[today] or sizes[today] <= 0:
            self.journal("check_skipped", minute=minute, reason="not enough history")
            self.state.last_check = key
            return
        current = 0 if position is None else (1 if position.type == mt5.POSITION_TYPE_BUY else -1)
        price = days.close[today, minute - 1]
        target = tactic.decide(today, minute - 1, price, current, levels)
        self.journal("check", minute=minute, price=price, current=current, target=target, size=float(sizes[today]))

        # A close and an entry never share a step. This check stays open, so the next poll sees the real position
        if position is not None and target != current:
            self.close(position, "tactic exit or reversal")
            if self.place_orders:
                self.pending_exit = True
                return
        self.state.last_check = key
        if target != 0 and target != current and volatility_spike(days, today, minute):
            self.journal("entry_skipped", reason="volatility spike", minute=minute)
        elif target != 0 and target != current and day not in NO_TRADE_DAYS:
            self.open(target, float(sizes[today]), float(levels.hard_stop[today]), account)

    def record_deals(self, server_now: datetime) -> None:
        """Copy new fills from the broker, including stop-outs, so results can be compared with the backtest."""
        start = (server_now - timedelta(days=3)).replace(tzinfo=timezone.utc)
        deals = mt5.history_deals_get(start, server_now.replace(tzinfo=timezone.utc) + timedelta(days=1))
        for deal in sorted(deals or (), key=lambda d: d.ticket):
            if deal.ticket > self.state.last_deal:
                self.state.last_deal = deal.ticket
                self.journal("deal", deal=deal._asdict())

    def day_opening_balance(self, account, server_now: datetime) -> float | None:
        """Today's starting balance, from history, so a bot started late still counts earlier losses."""
        midnight = server_now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
        deals = mt5.history_deals_get(midnight, midnight + timedelta(days=2))
        if deals is None:
            return None
        trades = [d for d in deals if d.type in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL)]
        return account.balance - sum(d.profit + d.commission + d.swap + d.fee for d in trades)

    def initial_balance(self) -> float | None:
        """The account's first deposit, which FTMO's total limit is measured from."""
        deals = mt5.history_deals_get(datetime(2000, 1, 1, tzinfo=timezone.utc), utc_now() + timedelta(days=2))
        deposits = sorted((d for d in deals or () if d.type == mt5.DEAL_TYPE_BALANCE), key=lambda d: d.time)
        return deposits[0].profit if deposits else None

    def step(self) -> None:
        account = mt5.account_info()
        if account is None or (terminal := mt5.terminal_info()) is None or not terminal.connected:
            self.alert("no_connection", error=str(mt5.last_error()))  # broker stops still protect an open trade
            mt5.initialize()
            return
        check_account(account, self.place_orders, self.login)
        if account.login != self.login:
            raise SystemExit("Refused: a different account is now logged in.")

        tick = mt5.symbol_info_tick(SYMBOL)
        found = self.positions()
        if tick is None or found is None:
            self.alert("no_data", error=str(mt5.last_error()))
            return

        day, minute, server_now = new_york_clock(utc_now())
        tick_time = datetime.fromtimestamp(tick.time, timezone.utc).replace(tzinfo=None)
        fresh = abs((server_now - tick_time).total_seconds()) <= STALE_SECONDS

        state = self.state
        server_day = server_now.date().isoformat()
        limits_known = self.update_limits(account, server_now, server_day)
        self.record_deals(server_now)

        in_session = 0 <= minute < LAST_MINUTE
        session_start = (server_now.replace(hour=0, minute=0, second=0) + timedelta(minutes=SESSION_OPEN)
                         + timedelta(hours=backtest.SERVER_HOURS_AHEAD_OF_NY))
        overnight = [p for p in found if datetime.fromtimestamp(p.time, timezone.utc).replace(tzinfo=None) < session_start]
        no_stop = [p for p in found if p.sl == 0]
        halted = state.halted_for_good or state.halted_day == server_day
        flatten = not in_session or halted or in_fomc_break(day, minute) or day in NO_TRADE_DAYS

        if not found:
            self.pending_exit = False
        if flatten or len(found) > 1 or overnight or no_stop or self.pending_exit:
            if found and (fresh or symbol_is_trading(server_now)):
                for position in found:
                    self.close(position, "limit, close-out, FOMC, duplicate, no stop or tactic exit")
            elif found:
                self.alert("cannot_close", reason="market closed", positions=len(found))
            save_state(state, self.state_path)
            return  # nothing opens in a step that had to close

        if not limits_known:
            self.alert("limits_unknown")
        elif not fresh:
            self.alert("stale_price", tick=tick_time, expected=server_now)
        else:
            self.last_alert = ""
            if minute in tactic.CHECK_MINUTES and state.last_check != f"{day} {minute}":
                self.check(day, minute, found[0] if found else None, account)
        save_state(state, self.state_path)

    def update_limits(self, account, server_now: datetime, server_day: str) -> bool:
        """Refresh the day and total limits. False if history can't be read, so no new trade may start."""
        state = self.state
        if state.start_balance == 0:
            state.start_balance = self.initial_balance() or 0.0
        if state.day != server_day:
            opening = self.day_opening_balance(account, server_now)
            if opening is None:
                return False
            state.day, state.day_start = server_day, max(opening, account.equity)
        if state.start_balance == 0:
            return False
        if account.equity <= state.start_balance * (1 + backtest.TOTAL_STOP):
            state.halted_for_good = True
        if account.equity <= state.day_start * (1 + backtest.DAY_STOP):
            state.halted_day = server_day
        return True

    def run(self, once: bool) -> None:
        try:
            while True:
                try:
                    self.step()
                except Exception as e:  # one bad poll mustn't end the bot and strand a position
                    self.journal("step_error", error=repr(e))
                if once:
                    return
                time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            for position in self.positions() or ():
                self.next_close_at = 0.0
                self.close(position, "stopped by the owner")
            print("Stopped. Check MT5 shows no open position.", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--place-demo-orders", action="store_true")
    parser.add_argument("--login", type=int)
    parser.add_argument("--once", action="store_true", help="run one step, for checking")
    args = parser.parse_args()
    if args.once and args.place_demo_orders:
        parser.error("--once can't place orders: nothing would watch the trade afterwards")
    if not mt5.initialize():
        raise SystemExit(f"Can't reach MT5: {mt5.last_error()}")
    try:
        account = mt5.account_info()
        check_account(account, args.place_demo_orders, args.login)
        if not mt5.symbol_select(SYMBOL, True):
            raise SystemExit(f"{SYMBOL} isn't available.")
        check_symbol(mt5.symbol_info(SYMBOL), account)
        everything, pending = mt5.positions_get(), mt5.orders_get()
        if everything is None or pending is None or pending or any(p.magic != MAGIC for p in everything):
            raise SystemExit("Refused: this account has other positions or pending orders, or they can't be read.")
        if args.place_demo_orders and not mt5.terminal_info().trade_allowed:
            raise SystemExit("Refused: turn on Algo Trading in MT5 first.")
        BOT_DIR.mkdir(parents=True, exist_ok=True)
        bot = Bot(args.place_demo_orders, account.login, file_stem(account.login, args.place_demo_orders))
        bot.journal("started", server=account.server, place_orders=args.place_demo_orders)
        bot.run(args.once)
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
