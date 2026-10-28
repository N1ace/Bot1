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


def test_futures_client_has_no_order_placement_yet():
    """Prompt A step: no orders. Remove this test when the execution step adds them."""
    text = (SRC / "cryptobot" / "exchange" / "futures_client.py").read_text(encoding="utf-8")
    for call in ("new_order", "new_algo_order", "place_multiple_orders", "test_order"):
        assert f"_api.{call}" not in text
