"""Exchange trading rules per symbol (from exchangeInfo) and Decimal rounding helpers.

Why Decimal and not float: floats can't represent most decimal numbers exactly
(0.1 + 0.2 = 0.30000000000000004). Binance rejects an order whose price or
quantity is not an exact multiple of the tick/step size, so every price and
quantity we send is a Decimal rounded with the helpers below.

Rounding rules used here:
- quantity: always rounded DOWN to the step size, so we never trade more than
  the risk manager approved.
- price: caller chooses the direction. A stop-loss for a long should round
  UP (closer to entry = slightly less risk) - see round_price.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

_ROUNDING = {"down": ROUND_FLOOR, "up": ROUND_CEILING, "nearest": ROUND_HALF_UP}


def to_decimal(value) -> Decimal:
    """Convert via str() so 0.1 becomes Decimal('0.1'), not 0.1000000000000000055..."""
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _round_to_step(value: Decimal, step: Decimal, mode: str) -> Decimal:
    units = (value / step).to_integral_value(rounding=_ROUNDING[mode])
    return (units * step).quantize(step)


@dataclass(frozen=True)
class SymbolRules:
    symbol: str
    tick_size: Decimal          # price must be a multiple of this (PRICE_FILTER)
    min_price: Decimal
    max_price: Decimal
    step_size: Decimal          # limit-order quantity step (LOT_SIZE)
    min_qty: Decimal
    max_qty: Decimal
    market_step_size: Decimal   # market-order quantity step (MARKET_LOT_SIZE)
    market_min_qty: Decimal
    market_max_qty: Decimal
    min_notional: Decimal       # minimum order value in USDT (MIN_NOTIONAL)

    @classmethod
    def from_exchange_info(cls, symbol_info: dict) -> SymbolRules:
        """Build from one entry of exchangeInfo['symbols'] (Binance field names)."""
        f = {flt["filterType"]: flt for flt in symbol_info["filters"]}
        price, lot = f["PRICE_FILTER"], f["LOT_SIZE"]
        market = f.get("MARKET_LOT_SIZE", lot)
        return cls(
            symbol=symbol_info["symbol"],
            tick_size=to_decimal(price["tickSize"]),
            min_price=to_decimal(price["minPrice"]),
            max_price=to_decimal(price["maxPrice"]),
            step_size=to_decimal(lot["stepSize"]),
            min_qty=to_decimal(lot["minQty"]),
            max_qty=to_decimal(lot["maxQty"]),
            market_step_size=to_decimal(market["stepSize"]),
            market_min_qty=to_decimal(market["minQty"]),
            market_max_qty=to_decimal(market["maxQty"]),
            min_notional=to_decimal(f.get("MIN_NOTIONAL", {}).get("notional", "0")),
        )

    # ---- rounding ------------------------------------------------------------

    def round_price(self, price, direction: str = "nearest") -> Decimal:
        """Round a price to the tick size. direction: 'down', 'up' or 'nearest'."""
        return _round_to_step(to_decimal(price), self.tick_size, direction)

    def round_qty(self, qty, market: bool = False) -> Decimal:
        """Round a quantity DOWN to the step size (never increases the position)."""
        step = self.market_step_size if market else self.step_size
        return _round_to_step(to_decimal(qty), step, "down")

    # ---- validation ----------------------------------------------------------

    def check(self, qty, price, market: bool = False) -> list[str]:
        """Return the reasons Binance would reject this order (empty list = OK).

        For market orders pass the current mark price as `price` - it is used
        for the minimum-notional check.
        """
        qty, price = to_decimal(qty), to_decimal(price)
        step = self.market_step_size if market else self.step_size
        min_q = self.market_min_qty if market else self.min_qty
        max_q = self.market_max_qty if market else self.max_qty
        problems = []
        if qty % step != 0:
            problems.append(f"quantity {qty} is not a multiple of step {step}")
        if qty < min_q:
            problems.append(f"quantity {qty} below minimum {min_q}")
        if qty > max_q:
            problems.append(f"quantity {qty} above maximum {max_q}")
        if not market and price % self.tick_size != 0:
            problems.append(f"price {price} is not a multiple of tick {self.tick_size}")
        if not market and not self.min_price <= price <= self.max_price:
            problems.append(f"price {price} outside [{self.min_price}, {self.max_price}]")
        if qty * price < self.min_notional:
            problems.append(f"order value {qty * price} below minimum notional {self.min_notional}")
        return problems

    def min_qty_for_notional(self, price, market: bool = True) -> Decimal:
        """Smallest valid quantity whose value reaches min_notional at this price."""
        price = to_decimal(price)
        step = self.market_step_size if market else self.step_size
        floor = self.market_min_qty if market else self.min_qty
        qty = _round_to_step(self.min_notional / price, step, "up")
        return max(qty, floor)
