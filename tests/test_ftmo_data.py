import lzma
import struct
from datetime import date

from ftmo.data import BAR, day_url, parse_day


def test_october_is_month_09_in_the_url():
    url = day_url("XAUUSD", date(2026, 10, 28), "BID")
    assert url.endswith("/XAUUSD/2026/09/28/BID_candles_min_1.bi5")


def test_bar_fields_come_out_in_the_right_order():
    # Sample bytes hold open, close, low, high, which is not the usual order
    bar = BAR.pack(60, 7726385, 7725388, 7724891, 7726391, 0.5)
    raw = lzma.compress(bar, format=lzma.FORMAT_ALONE)

    row = parse_day(raw, date(2026, 10, 28)).iloc[0]

    assert (row.open, row.high, row.low, row.close) == (7726.385, 7726.391, 7724.891, 7725.388)
    assert str(row.time) == "2026-10-28 00:01:00+00:00"


def test_closed_day_gives_no_bars():
    assert parse_day(b"", date(2026, 10, 24)).empty


def test_bar_struct_is_24_bytes():
    assert struct.calcsize(BAR.format) == 24
