"""Exchange access. This is the ONLY package allowed to import ccxt."""

from cryptobot.exchange.base import ExchangeClient

__all__ = ["ExchangeClient"]
