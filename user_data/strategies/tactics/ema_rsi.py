"""Starter tactic: buy RSI dips only while the faster EMA is above the slower EMA.

This is a placeholder so the bot can load, backtest, and dry-run.
Replace it, or add another module next to this file, when the real rules are ready.
"""

from pandas import DataFrame, Series

import talib.abstract as ta


class EmaRsiTactic:
    name = "ema_rsi"

    def __init__(self, rsi_entry: int = 35, rsi_exit: int = 70, ema_fast: int = 50, ema_slow: int = 200):
        self.rsi_entry = rsi_entry
        self.rsi_exit = rsi_exit
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow

    def populate(self, dataframe: DataFrame) -> DataFrame:
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["ema_fast"] = ta.EMA(dataframe, timeperiod=self.ema_fast)
        dataframe["ema_slow"] = ta.EMA(dataframe, timeperiod=self.ema_slow)
        return dataframe

    def should_enter_long(self, dataframe: DataFrame) -> Series:
        uptrend = dataframe["ema_fast"] > dataframe["ema_slow"]
        rsi_cross = (dataframe["rsi"] > self.rsi_entry) & (dataframe["rsi"].shift(1) <= self.rsi_entry)
        return uptrend & rsi_cross & (dataframe["volume"] > 0)

    def should_exit_long(self, dataframe: DataFrame) -> Series:
        rsi_hot = dataframe["rsi"] > self.rsi_exit
        lost_fast_trend = dataframe["close"] < dataframe["ema_fast"]
        return (rsi_hot | lost_fast_trend) & (dataframe["volume"] > 0)
