# CLAUDE.md — rules for working on this repo

This repo holds two things side by side:

- `src/cryptobot/` — **our own bot** (the main project).
- `docker-compose.yml` + `user_data/` — an existing **Freqtrade** setup, kept as a
  reference. Do not modify it unless the user asks.

## Safety rules (always follow, no exceptions)

1. **Paper / testnet by default.** Live trading needs ALL of:
   - `environment: live` and `live_trading: true` in `config/config.yaml`
   - env var `CRYPTOBOT_CONFIRM_LIVE=I_UNDERSTAND_REAL_MONEY`
   - live keys in `.env` (`BINANCE_<ACCOUNT>_LIVE_API_KEY/SECRET`)

   Claude must **never** enable live trading, set that env var, or edit those values.
2. **Secrets only in `.env`.** Never in code, config, logs, tests, commits or chat.
   Never read or print `.env`. `.env.example` holds placeholder names only.
3. **No withdrawal code, ever.** The exchange wrapper exposes an allowlist of methods
   and never passes calls through to raw CCXT. `tests/test_safety.py` fails if the
   word appears in source code (comments are fine, docstrings and strings are not).
4. **Every order goes through `RiskManager.review()`.** It enforces max order value,
   max open positions, max daily loss (halts new entries for the rest of the UTC day,
   persisted in SQLite) and a mandatory, correctly-placed stop-loss.
5. **Respect exchange rules:** `enableRateLimit` on, round with market precision,
   check min notional / lot size before sending. Never blindly retry order placement
   (duplicate orders); only read-only calls are retried.
6. **Ask the user before** installing packages or running anything that places orders
   (testnet included).

## Conventions

- Python 3.14, venv in `.venv/`, deps pinned in `requirements.txt`.
- Package layout `src/cryptobot/`, installed editable (`pip install -e .`).
- Only `exchange/` imports `ccxt`. Everything else uses dataclasses from `models.py`
  so the exchange library can be swapped (e.g. Binance official SDK).
- Strategies return `Signal`s only; they never place orders or see keys.
- One API key per Binance account/portfolio: env names `BINANCE_<ACCOUNT>_<ENV>_API_KEY`.
- Spot margin has **no Binance testnet** — margin work is tested with the paper broker.
- Indicators: plain pandas for now (`ewm` etc.); `pandas-ta` only if it supports our Python.
- Store all timestamps in UTC. Record every order, fill and fee (Israeli tax reporting).
- Keep code simple and commented; the user is learning — explain important decisions.

## Commands

```powershell
.\.venv\Scripts\Activate.ps1
python -m cryptobot              # run with config/config.yaml
python -m pytest                 # run tests
```
