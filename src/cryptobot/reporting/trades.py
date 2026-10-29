"""Trades and statistics for a period: today, yesterday, week, month, a day, or a date range.

Reads the trade log the bot writes (fills + funding payments in data/cryptobot.sqlite).
Days are UTC days, like everything else in the bot (Israel is UTC+2 in winter, +3 in summer).

Results are always split by environment (paper / demo / live): fake-money and
real-money results are never added together.

Words used:
  fill        one execution on the exchange (an order can fill in several pieces)
  trade       a "round trip": a position opened and later fully closed
  realized    profit/loss locked in when (part of) a position is closed, before fees
  net         realized - fees + funding  (what actually changed your balance)
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

PERIODS = ("today", "yesterday", "week", "month", "all")


def period_range(period: str, today: date | None = None) -> tuple[datetime, datetime, str]:
    """(start, end) UTC datetimes and a label. week = last 7 days, month = last 30 days (both incl. today)."""
    today = today or datetime.now(timezone.utc).date()
    start_of = lambda d: datetime(d.year, d.month, d.day, tzinfo=timezone.utc)  # noqa: E731
    end = start_of(today + timedelta(days=1))
    if period == "today":
        return start_of(today), end, f"today {today}"
    if period == "yesterday":
        y = today - timedelta(days=1)
        return start_of(y), start_of(today), f"yesterday {y}"
    if period == "week":
        return start_of(today - timedelta(days=6)), end, f"last 7 days ({today - timedelta(days=6)} to {today})"
    if period == "month":
        return start_of(today - timedelta(days=29)), end, f"last 30 days ({today - timedelta(days=29)} to {today})"
    if period == "all":
        return datetime(2000, 1, 1, tzinfo=timezone.utc), end, "all time"
    raise ValueError(f"period must be one of {PERIODS}")


def day_range(day: date) -> tuple[datetime, datetime, str]:
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1), f"{day}"


def custom_range(first: date, last: date) -> tuple[datetime, datetime, str]:
    start = datetime(first.year, first.month, first.day, tzinfo=timezone.utc)
    end = datetime(last.year, last.month, last.day, tzinfo=timezone.utc) + timedelta(days=1)
    return start, end, f"{first} to {last}"


def _read(db: str | Path, sql: str, params) -> pd.DataFrame:
    if str(db) != ":memory:" and not Path(db).exists():
        return pd.DataFrame()
    con = sqlite3.connect(db)
    try:
        return pd.read_sql_query(sql, con, params=params)
    except Exception:  # table not created yet
        return pd.DataFrame()
    finally:
        con.close()


def load_fills(db: str | Path, start: datetime, end: datetime) -> pd.DataFrame:
    df = _read(db, "SELECT * FROM fills WHERE timestamp >= ? AND timestamp < ? ORDER BY timestamp, id",
               (start.isoformat(), end.isoformat()))
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, format="ISO8601")
        df["signed_qty"] = df["quantity"].where(df["side"] == "buy", -df["quantity"])
        df["realized_pnl"] = df["realized_pnl"].fillna(0.0)
    return df


def load_funding(db: str | Path, start: datetime, end: datetime) -> pd.DataFrame:
    return _read(db, "SELECT * FROM funding_payments WHERE timestamp >= ? AND timestamp < ? ORDER BY timestamp",
                 (start.isoformat(), end.isoformat()))


@dataclass
class RoundTrip:
    environment: str
    symbol: str
    side: str                 # long / short
    opened: pd.Timestamp
    closed: pd.Timestamp | None   # None = still open at the end of the period
    quantity: float           # largest position size during the trade
    entry_price: float        # average entry
    exit_price: float | None  # average exit
    realized: float
    fees: float

    @property
    def net(self) -> float:
        """Realized - fees for this trade. Funding is shown separately in the statistics."""
        return self.realized - self.fees


def round_trips(fills: pd.DataFrame) -> list[RoundTrip]:
    """Group fills into trades: from flat -> position -> flat again, per environment and symbol.

    Must be given ALL fills from the very first one (not just a period), otherwise a
    trade that started earlier would be misread. Use trips_for_period() for periods.
    """
    trips: list[RoundTrip] = []
    if fills.empty:
        return trips
    for (env, symbol), f in fills.groupby(["environment", "symbol"], sort=False):
        pos, cur = 0.0, None
        for _, row in f.iterrows():
            q = row["signed_qty"]
            if cur is None:
                cur = dict(environment=env, symbol=symbol, side="long" if q > 0 else "short", opened=row["timestamp"],
                           qty=0.0, buy_val=0.0, buy_qty=0.0, sell_val=0.0, sell_qty=0.0, realized=0.0, fees=0.0)
            key = "buy" if q > 0 else "sell"
            cur[f"{key}_val"] += abs(q) * row["price"]
            cur[f"{key}_qty"] += abs(q)
            cur["realized"] += row["realized_pnl"]
            cur["fees"] += row["fee"]
            pos = round(pos + q, 12)
            cur["qty"] = max(cur["qty"], abs(pos))
            if pos == 0:
                trips.append(_finish(cur, row["timestamp"]))
                cur = None
        if cur is not None:
            trips.append(_finish(cur, None))
    return sorted(trips, key=lambda t: t.opened)


def _finish(c: dict, closed) -> RoundTrip:
    entry_key, exit_key = ("buy", "sell") if c["side"] == "long" else ("sell", "buy")
    avg = lambda k: c[f"{k}_val"] / c[f"{k}_qty"] if c[f"{k}_qty"] else None  # noqa: E731
    return RoundTrip(c["environment"], c["symbol"], c["side"], c["opened"], closed, c["qty"] or c[f"{entry_key}_qty"],
                     avg(entry_key), avg(exit_key), c["realized"], c["fees"])


def trips_for_period(all_fills: pd.DataFrame, start: datetime, end: datetime) -> list[RoundTrip]:
    """Trades touching the period: closed inside it, opened inside it, or still open at its end.

    Built from the complete fill history, so a trade opened before the period is read correctly.
    """
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    return [t for t in round_trips(all_fills)
            if t.opened < end_ts and (t.closed is None or t.closed >= start_ts)]


def _usdt_fees(f: pd.DataFrame) -> tuple[float, dict[str, float]]:
    """(fees paid in USDT, fees in other assets e.g. BNB) - never add different assets together."""
    if f.empty:
        return 0.0, {}
    asset = f["fee_asset"].fillna("USDT").replace("", "USDT")
    usdt = float(f.loc[asset == "USDT", "fee"].sum())
    other = f.loc[asset != "USDT"].groupby(asset[asset != "USDT"])["fee"].sum().to_dict()
    return usdt, {k: float(v) for k, v in other.items()}


def statistics(fills: pd.DataFrame, funding: pd.DataFrame, trips: list[RoundTrip] | None = None,
               start: datetime | None = None) -> dict[str, dict]:
    """Per environment: trades, wins, losses, win rate, realized, fees, funding, net, best/worst, volume.

    fills/funding: only the period's rows (money that moved inside the period).
    trips: trades from trips_for_period(); if None they are built from `fills` (whole history).
    """
    trips = round_trips(fills) if trips is None else trips
    start_ts = pd.Timestamp(start) if start else None
    out: dict[str, dict] = {}
    envs = set(fills["environment"]) if not fills.empty else set()
    envs |= set(funding["environment"]) if not funding.empty else set()
    envs |= {t.environment for t in trips}
    for env in sorted(envs):
        f = fills[fills["environment"] == env] if not fills.empty else fills
        fund = float(funding.loc[funding["environment"] == env, "amount"].sum()) if not funding.empty else 0.0
        env_trips = [t for t in trips if t.environment == env]
        closed = [t for t in env_trips if t.closed is not None and (start_ts is None or t.closed >= start_ts)]
        wins = [t.net for t in closed if t.net > 0]
        losses = [t.net for t in closed if t.net <= 0]
        realized = float(f["realized_pnl"].sum()) if not f.empty else 0.0
        fees, other_fees = _usdt_fees(f)
        out[env] = {
            "other_fees": other_fees,
            "trades_closed": len(closed),
            "trades_open": sum(1 for t in env_trips if t.closed is None),
            "fills": len(f),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(closed) if closed else math.nan,
            "realized": realized,
            "fees": fees,
            "funding": fund,
            "net": realized - fees + fund,
            "best": max((t.net for t in closed), default=math.nan),
            "worst": min((t.net for t in closed), default=math.nan),
            "avg_win": sum(wins) / len(wins) if wins else math.nan,
            "avg_loss": sum(losses) / len(losses) if losses else math.nan,
            "volume": float((f["quantity"] * f["price"]).sum()) if not f.empty else 0.0,
        }
    return out


# ---- plain-text output (terminal and Telegram) ---------------------------------------------

def _usd(x: float) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:+,.2f} USDT"


def format_trades(label: str, trips: list[RoundTrip]) -> str:
    lines = [f"Trades - {label} (UTC)  [net = realized - fees; funding is in the statistics]"]
    if not trips:
        lines.append("No trades in this period.")
        return "\n".join(lines)
    for t in trips:
        status = f"closed {t.closed:%Y-%m-%d %H:%M}" if t.closed is not None else "STILL OPEN"
        exit_txt = f" -> exit {t.exit_price:,.2f}" if t.exit_price else ""
        lines.append(f"[{t.environment}] {t.opened:%Y-%m-%d %H:%M} {t.symbol} {t.side.upper()} {t.quantity:g} "
                     f"@ {t.entry_price:,.2f}{exit_txt} | {status} | net {_usd(t.net)} (fees {t.fees:,.4f})")
    return "\n".join(lines)


def format_stats(label: str, stats: dict[str, dict]) -> str:
    lines = [f"Statistics - {label} (UTC)"]
    if not stats:
        lines.append("No trades or funding in this period.")
        return "\n".join(lines)
    for env, s in stats.items():
        wr = "n/a" if math.isnan(s["win_rate"]) else f"{s['win_rate']:.0%}"
        lines += [
            f"[{env}]",
            f"  trades closed {s['trades_closed']} (open {s['trades_open']}), fills {s['fills']}",
            f"  won {s['wins']}, lost {s['losses']}, win rate {wr}",
            f"  realized {_usd(s['realized'])} | fees {_usd(-s['fees'])} | funding {_usd(s['funding'])}",
            f"  NET {_usd(s['net'])}",
            f"  best trade {_usd(s['best'])}, worst {_usd(s['worst'])}, "
            f"avg win {_usd(s['avg_win'])}, avg loss {_usd(s['avg_loss'])}",
            f"  traded volume {s['volume']:,.0f} USDT",
        ]
        if s["other_fees"]:
            lines.append("  fees in other assets (not in NET): " +
                         ", ".join(f"{v:g} {k}" for k, v in s["other_fees"].items()))
    return "\n".join(lines)


EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)


def report(db: str | Path, start: datetime, end: datetime, label: str, what: str = "both") -> str:
    window_fills, funding = load_fills(db, start, end), load_funding(db, start, end)
    trips = trips_for_period(load_fills(db, EPOCH, end), start, end)   # full history -> correct grouping
    parts = []
    if what in ("trades", "both"):
        parts.append(format_trades(label, trips))
    if what in ("stats", "both"):
        parts.append(format_stats(label, statistics(window_fills, funding, trips, start)))
    return "\n\n".join(parts)
