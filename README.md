# cryptobot

A modular Binance trading bot in Python. **Spot** first; **spot margin**
(cross/isolated, borrow/repay), **USDⓈ-M futures** and **copy-trading lead** later.
Each mode is its own module and each Binance account uses its own API key.

> Safety first: the bot runs on **paper / testnet by default**. Live trading needs
> `live_trading: true` in the config **and** a confirmation environment variable.
> See [CLAUDE.md](CLAUDE.md) for all safety rules.

This repo also contains a separate **Freqtrade** setup (`docker-compose.yml`,
`user_data/`) kept as a reference. It is independent of `cryptobot`.

## Setup on Windows

Requires Python 3.11+ (developed on 3.14) and Git.

```powershell
cd C:\Users\forcr\OneDrive\Desktop\BOT

# 1. Create and activate a virtual environment (an isolated set of packages for this project)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
#    If PowerShell refuses with "running scripts is disabled", run once:
#    Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

# 2. Install dependencies, then the bot itself in "editable" mode
pip install -r requirements.txt
pip install -e .

# 3. Secrets
copy .env.example .env     # only if you don't have a .env yet
notepad .env
```

### Get Binance Spot Testnet keys

1. Go to <https://testnet.binance.vision>, log in with GitHub.
2. **Generate HMAC_SHA256 Key**. Copy the key and secret (the secret is shown once).
3. In `.env` set `BINANCE_SPOT_TESTNET_API_KEY=` and `BINANCE_SPOT_TESTNET_API_SECRET=`.

Testnet balances are fake and the testnet is reset roughly monthly. Its prices are
thin and noisy (big random wicks), so don't judge a strategy on testnet data.
For live keys later, Binance recommends **Ed25519** keys; always disable withdrawals
and restrict the key to your IP.

## Run

```powershell
.\.venv\Scripts\Activate.ps1
python -m cryptobot                 # uses config/config.yaml
python -m pytest                    # run the tests
```

The startup check prints the last 100 closed 1h candles for each pair and your
testnet balances. **It places no orders.** Logs go to the console and `logs/cryptobot.log`.

Switch `environment:` in `config/config.yaml`:

| environment | market data | balances | orders | keys needed |
|---|---|---|---|---|
| `paper`   | real Binance (public) | simulated from config | simulated (later) | none |
| `testnet` | testnet | testnet (fake) | testnet (later) | testnet keys |
| `live`    | real | real | **real money** | live keys + guard |

## Layout

```
config/config.yaml      settings: mode, environment, pairs, risk limits
src/cryptobot/
  __main__.py           entry point (python -m cryptobot)
  config.py             loads YAML + .env, validates, live-trading guard
  models.py             shared data types (Signal, OrderRequest, Position, Fill ...)
  logging_setup.py      console + rotating file logs, masks secrets
  exchange/             the ONLY code that talks to Binance (CCXT); paper broker
  data/                 candles (REST); WebSocket later
  strategies/           Strategy base class; strategies only emit Signals
  risk/                 RiskManager - approves/rejects every order
  execution/            order placement (next session)
  portfolio/            balances, positions, PnL
  storage/              SQLite log of orders, fills, risk events (tax records)
  notifications/        Telegram (stub)
  modes/                spot now; margin, futures, copy-trade lead later
  backtest/             backtester (later)
tests/                  pytest tests
logs/, data/            created at runtime, git-ignored
```

### How data flows

```
candles (data/) -> Strategy -> Signal -> sized OrderRequest
     -> RiskManager.review(order, portfolio snapshot) -> approved?
     -> execution/ (precision, min notional, clientOrderId) -> exchange/
     -> storage/ records every order + fill; notifications/ alerts
```

## Troubleshooting

- **`DLL load failed ... An Application Control policy has blocked this file`** -
  Windows Smart App Control blocked a compiled package file. This happened with
  pandas 3.1.0, which is why `requirements.txt` pins pandas 2.3.3.
- **`Timestamp for this request is outside of the recvWindow`** - sync your
  Windows clock (Settings → Time & language → Sync now).
