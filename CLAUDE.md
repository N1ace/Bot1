# CLAUDE.md — rules for working on this repo

**Current focus: Binance USDⓈ-M perpetual futures, DEMO environment.**
Read [binance-futures-bot-reference.md](binance-futures-bot-reference.md) for the decisions,
roadmap and official doc links. Fetch the official Binance docs listed there instead of guessing.
Strategy and risk model: [strategy-risk-spec.md](strategy-risk-spec.md) (daily Donchian
ensemble, 6 risk layers, validation gates). Live trading only after its section 7 gates pass.
Levels 0–6, allowed tactics and the advisor (bot suggests, owner decides):
[bot-roadmap-and-principles.md](bot-roadmap-and-principles.md). Never skip a level; the bot
never raises risk, leverage, parameters or its level by itself.
Since 2026-10-30 the goal is an active trading bot, not an investment bot: two sleeves (`config/core_big.yaml`,
`config/xs.yaml`) under `risk_profile: aggressive`. See the plan section in [README.md](README.md). Fix rules before a test,
run it once: every variant counts in the Deflated Sharpe (`data/variants_log.csv`).

This repo holds two things side by side:

- `src/cryptobot/` — **our own bot** (the main project). Spot mode is parked.
- `docker-compose.yml` + `user_data/` — an existing **Freqtrade** setup, kept as a
  reference. Do not modify it unless the user asks.

## Safety rules (always follow, no exceptions)

1. **Demo / testnet / paper by default.** Live trading needs ALL of:
   - `environment: live` and `live_trading: true` in `config/config.yaml`
   - env var `CRYPTOBOT_CONFIRM_LIVE=I_UNDERSTAND_REAL_MONEY`, typed per terminal session,
     never stored in `.env`
   - live keys in `.env` (`BINANCE_<ACCOUNT>_LIVE_...`)

   Claude must **never** enable live trading, set that env var, or edit those values.
2. **Secrets only in `.env` or key files outside the repo.** Never in code, config, logs,
   tests, commits or chat. Never print `.env` values or key material; when checking `.env`,
   print variable names and lengths only. listenKeys are secrets too (masked in logs).
3. **No withdrawal or transfer code.** Exchange wrappers expose an allowlist of methods and
   never pass calls through to the raw SDK/CCXT. `tests/test_safety.py` enforces this.
4. **Every order goes through `RiskManager.review()`.** Max order value, max open positions,
   max daily loss (halts, persisted in SQLite), mandatory stop-loss on the correct side.
   Futures: ISOLATED margin only, leverage hard cap 3x (`MAX_LEVERAGE_HARD_CAP`).
5. **Respect exchange rules:** `Decimal` for all prices/quantities, rounded with
   `SymbolRules` (tick, step, min notional). Never blindly resend an order: on timeout or
   HTTP 503 "unknown status", query the order by clientOrderId instead.
6. **Ask the user before** installing packages or running anything that places orders
   (demo and testnet included). Changing demo account settings (leverage, margin type)
   within the configured limits is part of the startup routine and allowed.

## Binance futures facts we rely on (verified 2026-10-28)

- REST: live `https://fapi.binance.com`, demo `https://demo-fapi.binance.com`.
- WebSockets moved to path routing (old `/ws`, `/stream` stopped delivering market data on
  live after 2026-04-23): `<base>/market/stream?streams=...` for klines/mark price,
  `<base>/public/...` for book ticker/depth, `<base>/private/ws/<listenKey>` for user data.
  Bases: live `wss://fstream.binance.com`, demo `wss://demo-fstream.binance.com`.
- Stop/TP/trailing orders go through the Algo Order API (`/fapi/v1/algoOrder`) since 2025-12-09.
- Official SDK: only GET/DELETE are retried (never new orders); default timeout 1 s (we use 10 s).
  All SDK exceptions inherit `binance_common.errors.Error`; `BadRequestError` is **not** a
  `ClientError`. Read the Binance error code with `api_error_code(e)`.

## Conventions

- Python 3.14, venv in `.venv/`, deps pinned in `requirements.txt` (+ `requirements-nodeps.txt`).
- Package layout `src/cryptobot/`, installed editable (`pip install -e .`).
- Only `exchange/` imports exchange libraries (official SDK, ccxt). Everything else uses our
  own dataclasses so the library can be swapped.
- Strategies return `Signal`s only; they never place orders or see keys.
- One API key per Binance account/environment: `BINANCE_<ACCOUNT>_<ENV>_API_KEY` +
  `_PRIVATE_KEY` (one-line body, fine for demo) / `_PRIVATE_KEY_PATH` (file outside OneDrive,
  for live) / `_API_SECRET` (HMAC).
- Indicators via `cryptobot.indicators` (pandas-ta wrapper, blanks warm-up rows).
- Store all timestamps in UTC. Record every order, fill, fee, and funding payment (Israeli tax).
- Keep code simple and commented; the user is learning — explain important decisions.

## Commands

```powershell
.\.venv\Scripts\Activate.ps1
python -m cryptobot --minutes 10   # futures demo: startup routine + streams, no orders
python -m pytest                   # run tests
```
