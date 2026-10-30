"""News & event-risk monitor, a separate service next to the trading bot.

It watches market stress, scheduled macro events and news, reports to the owner, and can
only make a bot MORE cautious. It holds no exchange keys and contains no order code.
See news-event-layer-spec.md.
"""
