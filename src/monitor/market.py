"""Layers 2 and 3: the hourly market check. No AI, only numbers.

The `eval_*` functions are pure, so each trigger can be tested alone. `hourly_check` fetches
public data, applies the triggers and returns what the owner should be told.

Actions only tighten. The kill switch asks a bot to close everything, so it needs market
proof: USDT below $0.97 on at least two venues. A headline or repeated API errors never set
it; the owner can, with `python -m monitor kill`.
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from monitor.flags import set_flag
from monitor.store import connect, get_kv, iso, set_kv, utc_now

log = logging.getLogger(__name__)

FAPI = "https://fapi.binance.com"
DERIBIT = "https://www.deribit.com/api/v2/public/get_volatility_index_data"
TIMEOUT = 20


# ---- triggers (pure) -----------------------------------------------------------------------

def eval_btc_move(closes: list[float], cfg: dict) -> str | None:
    """Last hour's return against a fixed limit and against its own recent volatility."""
    if len(closes) < 3 or min(closes) <= 0:
        return None
    rets = [b / a - 1 for a, b in zip(closes, closes[1:])]
    last, history = rets[-1], rets[:-1]
    sigma = statistics.pstdev(history) if len(history) >= 2 else 0.0
    if abs(last) > cfg["btc_1h_abs_return"]:
        return f"BTC moved {last:+.1%} in 1h"
    if sigma > 0 and abs(last) > cfg["btc_1h_sigma"] * sigma:
        return f"BTC moved {last:+.1%} in 1h ({abs(last) / sigma:.1f} sigma)"
    return None


def eval_dvol(closes: list[float], cfg: dict) -> str | None:
    """closes: daily DVOL, oldest first, last one current."""
    if len(closes) < 2:
        return None
    jump = closes[-1] - closes[-2]
    if jump >= cfg["dvol_jump_points_24h"]:
        return f"DVOL up {jump:.1f} points in 24h to {closes[-1]:.1f}"
    if len(closes) >= 90:
        rank = sum(v <= closes[-1] for v in closes) / len(closes)
        if rank > cfg["dvol_high_percentile"]:
            return f"DVOL {closes[-1]:.1f} is above {rank:.0%} of the last year"
    return None


def eval_funding(rates: list[float], oi: list[float], cfg: dict) -> tuple[str, str] | None:
    """(crowded direction, reason) when funding is extreme and open interest is at its 30-day high."""
    if len(rates) < 30 or len(oi) < 2:
        return None
    history = rates[:-1]
    sd = statistics.pstdev(history)
    if sd <= 0:
        return None
    z = (rates[-1] - statistics.fmean(history)) / sd
    if abs(z) > cfg["funding_z_block"] and oi[-1] >= max(oi):
        side = "long" if z > 0 else "short"
        return side, f"funding z {z:+.1f} with open interest at its 30-day high: {side} side crowded"
    return None


def eval_stablecoins(prices: dict[str, dict[str, float]], cfg: dict) -> tuple[str, str] | None:
    """prices: venue -> coin -> USD price. ('freeze' or 'kill', reason), or None."""
    need = cfg["stable_min_venues"]
    for coin in ("USDT", "USDC"):
        quotes = {v: p[coin] for v, p in prices.items() if coin in p}
        below_kill = [v for v, q in quotes.items() if q < cfg["stable_kill"]]
        below = [v for v, q in quotes.items() if q < cfg["stable_depeg"]]
        if coin == "USDT" and len(below_kill) >= need:
            return "kill", f"USDT below ${cfg['stable_kill']} on {', '.join(below_kill)}"
        if len(below) >= need:
            return "freeze", f"{coin} below ${cfg['stable_depeg']} on {', '.join(below)}"
    return None


def eval_liquidations(hours: list[float], cfg: dict) -> str | None:
    """hours: logged USDT liquidated per hour, last one the newest finished hour."""
    if len(hours) < cfg["liq_min_hours"]:
        return None
    history = sorted(hours[:-1])
    limit = history[min(int(len(history) * cfg["liq_percentile"]), len(history) - 1)]
    if hours[-1] > limit:
        return f"${hours[-1] / 1e6:.0f}M liquidated in 1h, above the 99th percentile of our logs (${limit / 1e6:.0f}M)"
    return None


# ---- public data ---------------------------------------------------------------------------

def _get(http, url: str, **params):
    resp = http.get(url, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def fetch_btc_closes(http) -> list[float]:
    """Closed 1h candles for the last 30 days."""
    now_ms = int(utc_now().timestamp() * 1000)
    rows = _get(http, f"{FAPI}/fapi/v1/klines", symbol="BTCUSDT", interval="1h", limit=721)
    return [float(k[4]) for k in rows if int(k[6]) < now_ms]


def fetch_dvol(http) -> list[float]:
    end = int(utc_now().timestamp() * 1000)
    start = end - 365 * 86_400_000
    data = _get(http, DERIBIT, currency="BTC", start_timestamp=start, end_timestamp=end, resolution="1D")
    return [float(row[4]) for row in sorted(data["result"]["data"])]


def fetch_funding(http) -> list[float]:
    return [float(r["fundingRate"]) for r in _get(http, f"{FAPI}/fapi/v1/fundingRate", symbol="BTCUSDT", limit=270)]


def fetch_open_interest(http) -> list[float]:
    rows = _get(http, f"{FAPI}/futures/data/openInterestHist", symbol="BTCUSDT", period="1d", limit=30)
    return [float(r["sumOpenInterestValue"]) for r in rows]


def fetch_stablecoins(http) -> dict[str, dict[str, float]]:
    """USD prices by venue. A venue that doesn't answer is left out; it can't confirm anything."""
    out: dict[str, dict[str, float]] = {}
    try:
        data = _get(http, "https://api.kraken.com/0/public/Ticker", pair="USDTUSD,USDCUSD")["result"]
        out["kraken"] = {coin: float(v["c"][0]) for coin, key in (("USDT", "USDT"), ("USDC", "USDC"))
                         for k, v in data.items() if key in k}
    except (requests.RequestException, KeyError, ValueError, TypeError) as e:
        log.warning("Kraken prices unavailable: %s", e)
    try:
        out["bitstamp"] = {coin: float(_get(http, f"https://www.bitstamp.net/api/v2/ticker/{coin.lower()}usd/")["last"])
                           for coin in ("USDT", "USDC")}
    except (requests.RequestException, KeyError, ValueError, TypeError) as e:
        log.warning("Bitstamp prices unavailable: %s", e)
    try:
        out["coinbase"] = {"USDT": float(_get(http, "https://api.coinbase.com/v2/prices/USDT-USD/spot")["data"]["amount"])}
    except (requests.RequestException, KeyError, ValueError, TypeError) as e:
        log.warning("Coinbase price unavailable: %s", e)
    return out


def binance_ok(http) -> bool:
    try:
        return http.get(f"{FAPI}/fapi/v1/ping", timeout=TIMEOUT).ok
    except requests.RequestException:
        return False


def _finished_liq_hours(db: str | Path, now: datetime) -> list[float]:
    hour = now.replace(minute=0, second=0, microsecond=0)
    with connect(db) as con:
        return [r[0] for r in con.execute("SELECT notional FROM liq_hours WHERE hour_utc < ? ORDER BY hour_utc",
                                          (iso(hour),)).fetchall()]


# ---- the check -----------------------------------------------------------------------------

def freeze_entries(db: str | Path, reason: str, hours: float, now: datetime) -> bool:
    return set_flag(db, "no_new_entries_until", now + timedelta(hours=hours), "stress", reason, now=now)


def hourly_check(db: str | Path, cfg: dict, http: requests.Session | None = None,
                 now: datetime | None = None) -> list[str]:
    """Runs every trigger. Returns alerts for the owner. One failed source doesn't stop the others."""
    http = http or requests.Session()
    now = now or utc_now()
    alerts: list[str] = []

    def source(name, fetch):
        try:
            return fetch(http)
        except (requests.RequestException, KeyError, ValueError, TypeError) as e:
            log.warning("%s unavailable: %s", name, e)
            return None

    closes = source("BTC candles", fetch_btc_closes)
    reason = eval_btc_move(closes, cfg) if closes else None
    if reason and freeze_entries(db, reason, cfg["freeze_hours"], now):
        alerts.append(f"{reason}. No new entries for {cfg['freeze_hours']}h. Check stops are live.")

    reason = eval_liquidations(_finished_liq_hours(db, now), cfg)
    if reason and freeze_entries(db, reason, cfg["freeze_hours"], now):
        alerts.append(f"{reason}. No new entries for {cfg['freeze_hours']}h.")

    dvol = source("DVOL", fetch_dvol)
    reason = eval_dvol(dvol, cfg) if dvol else None
    if reason and set_flag(db, "vol_mult", cfg["dvol_vol_mult"], "stress", reason, hours=cfg["dvol_mult_hours"], now=now):
        alerts.append(f"{reason}. New positions x{cfg['dvol_vol_mult']} for {cfg['dvol_mult_hours']}h.")

    rates, oi = source("funding", fetch_funding), source("open interest", fetch_open_interest)
    crowded = eval_funding(rates, oi, cfg) if rates and oi else None
    if crowded and set_flag(db, "block_adds_direction", crowded[0], "stress", crowded[1],
                            hours=cfg["block_adds_hours"], now=now):
        alerts.append(f"{crowded[1]}. No adds on that side for {cfg['block_adds_hours']}h.")

    stable = eval_stablecoins(fetch_stablecoins(http), cfg)
    if stable and stable[0] == "kill":
        if set_flag(db, "kill_switch", True, "stress", stable[1], now=now):
            alerts.append(f"KILL SWITCH SET: {stable[1]}. A bot must close everything and wait for you.")
    elif stable and freeze_entries(db, stable[1], cfg["freeze_hours"], now):
        alerts.append(f"{stable[1]}. Entries frozen for {cfg['freeze_hours']}h.")

    if binance_ok(http):
        set_kv(db, "binance_fails", "0")
    else:
        fails = int(get_kv(db, "binance_fails", "0")) + 1
        set_kv(db, "binance_fails", str(fails))
        if fails >= cfg["binance_fail_limit"]:
            reason = f"Binance API failed {fails} hourly checks in a row"
            if freeze_entries(db, reason, cfg["freeze_hours"], now):
                alerts.append(f"{reason}. Entries frozen. Please check if trading or payouts are halted.")
    return alerts


def log_liquidation_hour(db: str | Path, hour: datetime, notional: float) -> None:
    hour = hour.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    with connect(db) as con:
        con.execute("INSERT OR REPLACE INTO liq_hours VALUES (?,?)", (iso(hour), notional))
