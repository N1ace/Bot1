"""Binance spot strategy that runs one or more local tactics.

Add a tactic by creating a class in ``user_data/strategies/tactics/`` and
appending an instance to ``CustomTacticStrategy.tactics``.
"""

from pandas import DataFrame

from freqtrade.strategy import IStrategy, IntParameter

from tactics.ema_rsi import EmaRsiTactic


class CustomTacticStrategy(IStrategy):
    INTERFACE_VERSION = 3

    # Spot on Binance. Shorting stays off until we switch the config to futures.
    can_short = False

    timeframe = "15m"
    startup_candle_count = 200
    process_only_new_candles = True

    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # Risk controls live here, not inside a tactic. Backtest before changing them.
    minimal_roi = {
        "0": 0.03,
        "60": 0.015,
        "180": 0.005,
    }
    stoploss = -0.05
    trailing_stop = True
    trailing_stop_positive = 0.01
    trailing_stop_positive_offset = 0.02
    trailing_only_offset_is_reached = True

    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }
    order_time_in_force = {"entry": "GTC", "exit": "GTC"}

    rsi_entry = IntParameter(20, 45, default=35, space="buy", optimize=True)
    rsi_exit = IntParameter(55, 85, default=70, space="sell", optimize=True)

    plot_config = {
        "main_plot": {
            "ema_fast": {"color": "blue"},
            "ema_slow": {"color": "orange"},
        },
        "subplots": {
            "RSI": {
                "rsi": {"color": "red"},
            },
        },
    }

    def _tactic(self) -> EmaRsiTactic:
        return EmaRsiTactic(
            rsi_entry=int(self.rsi_entry.value),
            rsi_exit=int(self.rsi_exit.value),
        )

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return self._tactic().populate(dataframe)

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        tactic = self._tactic()
        enter = tactic.should_enter_long(dataframe)
        dataframe.loc[enter, "enter_long"] = 1
        dataframe.loc[enter, "enter_tag"] = tactic.name
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        exit_long = self._tactic().should_exit_long(dataframe)
        dataframe.loc[exit_long, "exit_long"] = 1
        dataframe.loc[exit_long, "exit_tag"] = "ema_rsi"
        return dataframe
