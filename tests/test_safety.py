"""Guards against code that could send funds off the exchange.

Scans every Python file in src/ and fails if a forbidden word appears in code,
names or strings. Comments are ignored, so documentation can still say "never do X".
"""

import io
import tokenize
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
# Wallet transfers (spot <-> margin) are NOT blocked: margin mode will need them.
FORBIDDEN = ("withdraw",)


def test_no_fund_movement_code_in_src():
    offenders = []
    for path in SRC.rglob("*.py"):
        tokens = tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline)
        for tok in tokens:
            if tok.type == tokenize.COMMENT:
                continue
            if any(word in tok.string.lower() for word in FORBIDDEN):
                offenders.append(f"{path.relative_to(SRC)}:{tok.start[0]}: {tok.string[:60]}")
    assert not offenders, "Forbidden fund-movement code found:\n" + "\n".join(offenders)


def test_only_exchange_package_imports_exchange_libraries():
    offenders = [
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if "exchange" not in p.parts
        and any(lib in p.read_text(encoding="utf-8")
                for lib in ("import ccxt", "binance_sdk_", "from binance_common.configuration"))
    ]
    assert not offenders, f"exchange library imported outside exchange/: {offenders}"


def test_futures_client_order_allowlist():
    """Only these SDK calls may change orders. Adding one is a deliberate, reviewed change."""
    import re
    text = (SRC / "cryptobot" / "exchange" / "futures_client.py").read_text(encoding="utf-8")
    calls = set(re.findall(r"self\._api\.(\w+)\(", text))
    order_calls = {c for c in calls if any(w in c for w in ("order", "cancel", "modify", "batch"))}
    assert order_calls == {"new_order", "query_order", "new_algo_order", "current_all_open_orders",
                           "current_all_algo_open_orders", "cancel_algo_order"}


def test_stop_orders_close_position_on_mark_price():
    text = (SRC / "cryptobot" / "exchange" / "futures_client.py").read_text(encoding="utf-8")
    assert 'close_position=NewAlgoOrderClosePositionEnum("true")' in text
    assert "working_type=NewAlgoOrderWorkingTypeEnum.MARK_PRICE" in text
