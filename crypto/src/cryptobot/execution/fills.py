"""Turn user-data-stream ORDER_TRADE_UPDATE events into Fill records.

An order can fill in several pieces; Binance sends one ORDER_TRADE_UPDATE with
execution type x="TRADE" per piece. Each piece becomes one Fill row in SQLite
(price, quantity, commission, realized PnL) - the raw material for tax reports.
"""

from __future__ import annotations

from datetime import datetime, timezone

from cryptobot.models import Fill, Side


def fill_from_order_update(event: dict) -> Fill | None:
    if event.get("e") != "ORDER_TRADE_UPDATE":
        return None
    o = event.get("o") or {}
    if o.get("x") != "TRADE":
        return None
    return Fill(
        order_id=str(o["i"]),
        symbol=o["s"],
        side=Side(o["S"].lower()),
        quantity=float(o["l"]),               # this piece's quantity
        price=float(o["L"]),                  # this piece's price
        fee=float(o.get("n") or 0),           # commission
        fee_asset=o.get("N") or "",
        timestamp=datetime.fromtimestamp(o.get("T", event.get("E", 0)) / 1000, tz=timezone.utc),
        realized_pnl=float(o.get("rp") or 0),
        client_order_id=o.get("c", ""),
        trade_id=str(o.get("t", "")),
    )
