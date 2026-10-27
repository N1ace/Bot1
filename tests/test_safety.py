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


def test_only_exchange_package_imports_ccxt():
    offenders = [
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if "exchange" not in p.parts and "import ccxt" in p.read_text(encoding="utf-8")
    ]
    assert not offenders, f"ccxt imported outside exchange/: {offenders}"
