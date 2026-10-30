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

- [x] ~~N1. Run the risk-reviewer agent on `src/monitor/` (spec section 10)~~ (done 2026-10-31)
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

## News monitor: safety review 2026-10-31

Fixed now, because alerts are the monitor's whole value today:

- [x] ~~R1. Bad AI output skipped the raw headlines and could lose a big-move alert (S1)~~ (done 2026-10-31)
- [x] ~~R2. The big-move thread could die silently (B2)~~ (done 2026-10-31)
- [x] ~~R3. One error in the hourly job could cancel event blackouts (B3)~~ (done 2026-10-31)
- [x] ~~R4. Kill switch alert could be lost; cooldown started before delivery (B5)~~ (done 2026-10-31)
- [x] ~~R5. Real Binance warnings and opposite moves could be suppressed; DVOL alert repeated hourly (S2, S3)~~ (done 2026-10-31)
- [x] ~~R6. Calendar running out or partly failing wasn't noticed; no refresh after the PC was off (B7)~~ (done 2026-10-31)
- [x] ~~R7. Blind data sources weren't reported (S5)~~ (done 2026-10-31)
- [x] ~~R8. Crashes under pythonw were invisible (S8; job errors now reach Telegram too)~~ (done 2026-10-31)
- [x] ~~R9. Fake closing tag and planted links in AI text (S4)~~ (done 2026-10-31)

Before the order bot connects:

- [ ] R10. Fail-safe must prove real checks ran: record last good hourly check and last stream message, block entries if stale (B1)
- [ ] R11. Kill switch must reject bad prices: sane band, bid/ask mids, two checks in a row, exact Kraken keys (B4)
- [ ] R12. Fail-closed `safe_read_view()`, config value for the time limit, `Decimal` gap sizing through `RiskManager.review()` (B6)
- [ ] R13. Only one monitor process at a time (S6)
- [ ] R14. Move the monitor database out of OneDrive and turn on WAL (S7)
- [ ] R15. Validate safety settings at load; `stable_min_venues` at least 2, no negative gap settings (S9)
- [ ] R16. Bot-side alert when the monitor is down (it can't report its own death)
- [ ] R17. Test gaps: real calendar snapshots, private data test that can fail, thread death, two instances

Smaller notes:

- [ ] R18. Attach log masking, load only needed names from `.env`, pin aiohttp, requests, pydantic, tzdata
- [ ] R19. Liquidation counter resets on restart; blackout can open up to 59 minutes late, so the bot should read `events` too
- [ ] R20. FOMC notation votes stored as decisions; a Dec/Jan meeting gets the wrong year; a UTC format in the BLS file would shift times
- [ ] R21. Spec table says API errors set the kill switch; the code follows the safer rule. Correct the spec
- [ ] R22. Kalshi odds and an AI weekly review aren't built (spec section 4 and 8)
