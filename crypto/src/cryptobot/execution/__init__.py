"""Order execution - planned for the next session.

Responsibilities (not implemented yet):
- Turn an approved RiskDecision into an exchange order.
- Round quantity/price to the market's precision and check min notional
  BEFORE sending (ExchangeClient.market_rules).
- Give every order a unique clientOrderId so a timed-out request can be
  checked instead of blindly retried (avoids duplicate orders).
- Place the stop-loss order right after the entry fills.
- Record every order and fill in TradeStore.
"""
