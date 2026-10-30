# Fixes to do

From the safety review of 2026-10-30 (commits 11ee62f, 32466df). Finished items are ~~crossed out~~
with the date. Keep this file up to date: nothing moves to the next level with its list still open.

## Before Level 1 (paper results must be trustworthy; no money at risk)

- [x] ~~L1. Paper funding is never recorded, so `/stats` leaves it out (`paper/runner.py`)~~ (done 2026-10-30)
- [x] ~~L2. Replay check misses fills that vanish, and ignores fee and profit (`paper/runner.py`)~~ (done 2026-10-30)
- [x] ~~L3. A temporary data gap can be booked as a delisting at a 2% penalty (`backtest/engine.py`, `backtest/universe.py`)~~ (done 2026-10-30)
- [x] ~~L4. Start date, capital and sleeve settings are not locked (`config/paper.yaml`, `paper/runner.py`)~~ (done 2026-10-30)
- [x] ~~L5. The exit test "paper behaves like the backtest" can never fail (`bot-roadmap-and-principles.md`)~~ (done 2026-10-30)

Smaller Level 1 items from the same review:

- [x] ~~L6. A paper failure still counts as a clean day (`observer/daily.py`)~~ (done 2026-10-30)
- [x] ~~L7. Paper crashes before its first day of data, 2026-11-02 (`paper/runner.py`)~~ (done 2026-10-30)
- [x] ~~L8. Report has no as-of date, doesn't show liquidation, and rounding can show phantom positions (`paper/runner.py`)~~ (done 2026-10-30)
- [x] ~~L9. Old text says Level 1 isn't built (`observer/daily.py`)~~ (done 2026-10-30)
- [x] ~~L10. A failed coin-list download could silently miss dead coins (`backtest/universe.py`)~~ (done 2026-10-30)
- [x] ~~L11. Reports hide database read errors and show "no trades" (`reporting/trades.py`)~~ (done 2026-10-30)
- [x] ~~L12. Paper safety test only checks one import style in one file (`tests/test_paper.py`)~~ (done 2026-10-30)

Still to do for Level 1's exit, not for its start:

- [ ] L13. Write the script that compares paper results with same-length backtest windows (the new exit test from L5)

## Before Level 2 (real demo orders)

Blockers:

- [ ] D1. Adding to a position keeps the old stop, which can sit beyond liquidation; heat is understated (`strategies/xs_momentum.py`). Re-run the coin-ranking test once after this fix.
- [ ] D2. Coin-ranking sleeve never checks stop against liquidation (`strategies/xs_momentum.py`)
- [ ] D3. Coin-ranking sleeve skips cap cuts of up to 20% (`strategies/xs_momentum.py`)
- [ ] D4. Sleeves don't fit the order rules: `RiskManager.review` refuses adds, limits allow 2 positions at $100, leverage is one global setting, and one-way mode nets the sleeves (`risk/manager.py`, `config/config.yaml`). Binance demo appears to have no sub-accounts, so plan one account, net positions and a per-sleeve ledger.
- [ ] D5. Replay can't be the trading state: need saved breaker state, a sleeve ledger matched to the exchange, and an owner-review path after the kill switch
- [ ] D6. Bad data on a Sunday closes every position (`strategies/xs_momentum.py`)

Should fix:

- [ ] D7. Limit checks: `.nan` and negative values pass, `losing_streak_mult` above 1 raises risk, many fields unchecked, strategy constructors skip validation (`risk/portfolio.py`)
- [ ] D8. Risk profile isn't tied to the bot level; hard kill-switch limit is 0.40 while the plan says -35% (`risk/portfolio.py`)
- [ ] D9. Stops and positions on days with no candle (`backtest/engine.py`). Equity marking was fixed under L3; stops and late fills are still open.
- [ ] D10. `cluster: []` assumes a hedge that didn't show up: a funding block can leave four coins on one side (`config/xs.yaml`)
- [ ] D11. A coin stopped on Sunday can re-enter on Monday (`strategies/xs_momentum.py`)
- [ ] D12. Execution must handle a stop that price has already passed (Binance refuses it)
- [ ] D13. Validation is too kind in crashes: alt stops fill near the stop price, `priceProtect` isn't modelled, costs test only doubles slippage (`backtest/engine.py`)

Notes to check at Level 2:

- [ ] D14. `qty_step` and `min_notional` are missing for most coins, use real `SymbolRules`
- [ ] D15. Maintenance margin 0.4% is used for all coins; alt tiers are higher, and engine liquidation is account-level
- [ ] D16. No limit across the two sleeves combined, and no intraday breaker
- [ ] D17. Universe data: removed coins may lack their final month, REST candles have no sanity checks, a reused ticker would be joined
- [ ] D18. Tax export must exclude `paper-*` rows
- [ ] D19. `tests/test_safety.py` misses "transfer" and `from ccxt import`
- [ ] D20. Import the leverage cap constant instead of the literal 3 (`risk/portfolio.py`)

Before going live: all section 7 gates must pass, and check whether the real account can use sub-accounts.

## News monitor: still to do

- [ ] N1. Run the risk-reviewer agent on `src/monitor/` (spec section 10)
- [ ] N2. The order bot must call `read_view()` before every entry and use `gap_capped_qty()` (bot doesn't exist yet)
- [ ] N3. Kill switch: the order bot must close positions when it's set (the monitor only sets the flag)
- [ ] N4. Liquidation stress action: tighten the stop to 1x ATR for positions on the losing side (bot's job)
- [ ] N5. Binance announcement stream: needs an HMAC key with a secret on the real account, which we don't have (`wss://api.binance.com/sapi/wss`)
- [ ] N6. CPI adaptive size rule (R-squared of BTC vs CPI surprise): needs consensus forecasts, no free official source found
- [ ] N7. Backtest each layer (spec section 8) and stress replays. Keep a layer only if it earns it
- [ ] N8. Withdrawal halts: no data source yet, so the owner sets `python -m monitor kill`
- [ ] N9. VPS, so it watches while the PC is off
- [ ] N10. First real AI briefing, once `ANTHROPIC_API_KEY` is in `.env` (request shape checked offline only)
- [ ] N11. Polish pass on alert texts after a few weeks of real use
