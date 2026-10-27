"""One module per trading mode. Each wires the shared parts together with its own API key.

spot.py            - implemented (startup check)
margin.py          - later: cross/isolated, borrow/repay (no Binance testnet -> paper broker)
futures.py         - later: USD-M futures
copytrade_lead.py  - later: futures copy-trading lead account
"""
