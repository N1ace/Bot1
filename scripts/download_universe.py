"""Download daily candles of every USDT perpetual, dead or alive: python scripts/download_universe.py"""

import logging

from cryptobot.backtest.universe import Universe

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
u = Universe()
print("symbols:", u.refresh_symbols(), flush=True)
u.download_bars()
print("rows:", u.db.execute("SELECT COUNT(DISTINCT symbol), COUNT(*) FROM ubars").fetchone(), flush=True)
print(u.db.execute("SELECT status, underlying_type, COUNT(*) FROM usymbols GROUP BY 1,2").fetchall())
print("symbols with no bars:", [r[0] for r in u.db.execute(
    "SELECT symbol FROM usymbols WHERE symbol NOT IN (SELECT DISTINCT symbol FROM ubars)")][:40])
