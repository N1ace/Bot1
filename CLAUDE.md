# CLAUDE.md - rules for working on this repo

**Current focus (2026-10-31): a day-trading bot for an FTMO funded account.** Plan and decisions:
[ftmo-plan.md](ftmo-plan.md). New work lives in the repo root.

**Crypto is set aside in [crypto/](crypto/).** That folder holds the original crypto bot, the news monitor and the
crypto research lab, with its own config, data and `.env`. Don't change it until the owner says so. The saved
version is also tag `research-bot-v1`. Its Level 0 daily job keeps running (task `cryptobot-daily`, working folder
`crypto/`). The news monitor is stopped; its task can be reinstalled with `crypto/scripts/install_monitor_task.ps1`.
Binance facts, conventions and commands below apply to the crypto project.

`docker-compose.yml` + `user_data/` are an old **Freqtrade** setup kept as a reference. Don't modify them.

Fix a tactic's rules before testing it, and run each test once: every attempt counts against skill-or-luck.

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
cd crypto; python -m cryptobot --minutes 10   # futures demo: startup routine + streams, no orders
python -m pytest                   # run tests
```
