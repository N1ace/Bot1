"""Parsing of market stream messages and user-data-stream events (no network)."""

from decimal import Decimal

from cryptobot.data.market_stream import ClosedCandle, MarkPrice, parse_message, stream_url
from cryptobot.data.user_stream import funding_rows
from cryptobot.storage import TradeStore


def kline_msg(closed: bool):
    return {"stream": "btcusdt@kline_1h", "data": {
        "e": "kline", "E": 1793100000000, "s": "BTCUSDT",
        "k": {"t": 1793098800000, "T": 1793102399999, "s": "BTCUSDT", "i": "1h",
              "o": "79968.0", "c": "79910.0", "h": "80154.0", "l": "79858.8", "v": "293.9", "x": closed}}}


def test_stream_url_uses_market_path():
    url = stream_url("wss://demo-fstream.binance.com", ["BTCUSDT", "ETHUSDT"], "1h")
    assert url == ("wss://demo-fstream.binance.com/market/stream?streams="
                   "btcusdt@kline_1h/btcusdt@markPrice/ethusdt@kline_1h/ethusdt@markPrice")


def test_open_kline_is_ignored():
    assert parse_message(kline_msg(closed=False)) is None


def test_closed_kline_becomes_candle():
    c = parse_message(kline_msg(closed=True))
    assert isinstance(c, ClosedCandle)
    assert c.symbol == "BTCUSDT" and c.close == Decimal("79910.0")
    assert c.open_time.isoformat() == "2026-10-27T11:00:00+00:00"


def test_mark_price():
    m = parse_message({"stream": "btcusdt@markPrice", "data": {
        "e": "markPriceUpdate", "E": 1, "s": "BTCUSDT", "p": "79950.1", "i": "79940", "r": "0.00010000",
        "T": 1793116800000}})
    assert isinstance(m, MarkPrice)
    assert m.mark_price == Decimal("79950.1") and m.funding_rate == Decimal("0.0001")


FUNDING_EVENT = {"e": "ACCOUNT_UPDATE", "E": 1793116800123, "T": 1793116800100, "S": "BTCUSDT",
                 "a": {"m": "FUNDING_FEE", "B": [{"a": "USDT", "wb": "4999.5", "cw": "4999.5", "bc": "-0.5"}],
                       "P": []}}


def test_funding_rows():
    assert funding_rows(FUNDING_EVENT) == [("USDT", -0.5)]
    assert funding_rows({"e": "ACCOUNT_UPDATE", "a": {"m": "ORDER", "B": []}}) == []


def test_store_records_events_and_funding():
    store = TradeStore(":memory:", "demo", "futures", "FUTURES")
    store.record_stream_event(FUNDING_EVENT)
    store.record_stream_event({"e": "ORDER_TRADE_UPDATE", "E": 2, "o": {"s": "ETHUSDT", "X": "NEW"}})
    store.record_funding(FUNDING_EVENT["E"], "BTCUSDT", "USDT", -0.5)
    assert store.count_stream_events() == {"ACCOUNT_UPDATE": 1, "ORDER_TRADE_UPDATE": 1}
    symbols = [r[0] for r in store._db.execute("SELECT symbol FROM stream_events ORDER BY id")]
    assert symbols == ["BTCUSDT", "ETHUSDT"]
    assert store._db.execute("SELECT amount FROM funding_payments").fetchone()[0] == -0.5
