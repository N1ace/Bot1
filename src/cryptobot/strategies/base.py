"""Base class for all strategies.

A strategy looks at candles and says "I'd like to buy/sell, with this stop-loss".
That's all. It does NOT decide position size, does NOT place orders and never sees
API keys. Sizing and safety belong to the risk manager and executor.

The same strategy class runs live, on testnet, in paper mode and in the backtester,
so what you backtest is exactly what you trade.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from cryptobot.models import Signal


class Strategy(ABC):
    name: str = "base"
    timeframe: str = "1h"
    # Candles needed before the strategy can say anything (e.g. a 50-EMA needs ~50).
    min_candles: int = 50

    def __init__(self, params: dict | None = None):
        """params: strategy settings from config (e.g. EMA lengths), so you can
        tune them without editing code."""
        self.params = params or {}

    @abstractmethod
    def generate_signal(self, symbol: str, candles: pd.DataFrame) -> Signal | None:
        """Look at CLOSED candles (oldest first) and return a Signal, or None to do nothing.

        Rules for implementations:
        - Use only the data passed in (no look-ahead, no network calls).
        - Every entry Signal must include stop_loss.
        - Return None when there is not enough data (len(candles) < min_candles).
        """

    def has_enough_data(self, candles: pd.DataFrame) -> bool:
        return len(candles) >= self.min_candles
