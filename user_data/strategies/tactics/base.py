"""Contract for custom entry and exit tactics.

A tactic adds columns in ``populate`` and returns boolean masks for entries
and exits. The strategy applies risk settings (stoploss, ROI) on top.
"""

from typing import Protocol

from pandas import DataFrame, Series


class Tactic(Protocol):
    name: str

    def populate(self, dataframe: DataFrame) -> DataFrame:
        """Add indicator columns used by this tactic. Must return the same frame."""

    def should_enter_long(self, dataframe: DataFrame) -> Series:
        """True on candles that should open a long."""

    def should_exit_long(self, dataframe: DataFrame) -> Series:
        """True on candles that should close a long."""
