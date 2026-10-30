# cryptobot

A modular Binance trading bot in Python. **Current focus: USDⓈ-M perpetual futures**
(BTCUSDT, ETHUSDT) on Binance **Demo Trading**. Spot is parked; copy-trading lead later.
Each Binance account uses its own API key.

Decisions, roadmap and official doc links: [binance-futures-bot-reference.md](binance-futures-bot-reference.md).

> Safety first: the bot runs on **demo by default**. Live trading needs
> `live_trading: true` in the config **and** a confirmation typed in the terminal.
> Futures use **ISOLATED margin only** and leverage is **capped at 3x in code**.
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

# 2. Install dependencies (two steps - see requirements-nodeps.txt for why),
#    then the bot itself in "editable" mode
pip install -r requirements.txt
pip install --no-deps -r requirements-nodeps.txt
pip install -e .

# 3. Secrets
copy .env.example .env     # only if you don't have a .env yet
notepad .env
```

### Futures Demo keys

1. Log in to Binance → **Demo Trading** → API Management (demo.binance.com).
2. Create a **self-generated Ed25519** key (generate the pair with Binance's Key Generator,
   upload the public key). Permissions: Reading + Futures. Never withdrawals.
3. In `.env`:
   ```
   BINANCE_FUTURES_DEMO_API_KEY=<API key Binance showed you>
   BINANCE_FUTURES_DEMO_PRIVATE_KEY=<the one long line between BEGIN/END PRIVATE KEY>
   ```
   For a live key later, keep the key in a file outside OneDrive and use
   `BINANCE_FUTURES_LIVE_PRIVATE_KEY_PATH=` instead.

The demo account must be in **One-way** position mode and **Single-Asset** mode
(Futures → Settings). The bot checks this and refuses to run otherwise.

## Run

```powershell
.\.venv\Scripts\Activate.ps1
python -m cryptobot --minutes 10    # futures demo, uses config/config.yaml
python -m pytest                    # run the tests
```

What it does now (**it places no orders**):

1. **Startup routine** - checks one-way + single-asset mode, sets ISOLATED margin and
   2x leverage for each pair, loads the exchange rules (price tick, quantity step,
   minimum order value).
2. **Account snapshot** - balances, open positions, open orders and stop (algo) orders.
3. **Streams for N minutes** - live 1h candles + mark price (logs each *closed* candle),
   and the user data stream (every account event saved to SQLite, funding payments
   saved separately for tax). Both reconnect automatically.
4. **Summary** - messages received, closed candles, mark prices and funding rates,
   account events, reconnects.

Logs go to the console and `logs/cryptobot.log`; data to `data/cryptobot.sqlite`.

## Level 0 - the daily observer (current level)

The bot grows in levels (see `bot-roadmap-and-principles.md`). `level:` in `config/config.yaml`
is set **only by you**; the bot refuses anything above it. Level 0 = data + reports, no orders.

```powershell
python -m cryptobot daily              # update data, market state, Telegram report (+ weekly review on Sundays' close)
python -m cryptobot daily --no-send    # same without Telegram
powershell -ExecutionPolicy Bypass -File scripts\install_daily_task.ps1           # schedule it daily at 03:30
powershell -ExecutionPolicy Bypass -File scripts\install_daily_task.ps1 -Remove   # remove the schedule
```

- Daily report: market state, positions, risk usage, what the bot did, one tip (in `reports/daily/`).
- Weekly review: `reports/weekly-<year>-W<week>.md` + Telegram, with up to 3 suggestions.
- Answer suggestions in Telegram with `/approve N` or `/reject N` (read at the next daily run).
  Approving only **records** your answer - the bot then tells you which config line to change.
- Level 0 exit gate: 14 clean daily runs in a row (no crash, no data gap).

### Trades and statistics

```powershell
python -m cryptobot trades --period today      # or yesterday / week / month / all
python -m cryptobot trades --day 2026-10-28
python -m cryptobot trades --from 2026-10-01 --to 2026-10-31
python -m cryptobot stats --period month       # profit/loss, trades, win rate, fees, funding, best/worst
```

Telegram (answered at the next daily run): `/today` `/yesterday` `/week` `/month` `/day 2026-10-28`
`/stats month`. Days are UTC. Paper, demo and live results are always shown separately.
- Telegram setup: create a bot with @BotFather, put `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`.

## Backtesting (strategy-risk-spec.md steps 1-2)

```powershell
python scripts/download_history.py        # BTCUSDT + ETHUSDT daily candles, mark candles, funding since 2020
python scripts/run_backtest.py            # buy & hold sanity check, with and without funding
```

- `--extended` (download and backtest) adds history from 2017-08: Binance spot candles before
  futures existed, Bybit funding 2018-19, and an assumed 0.01%/8h before that (labelled in output).
- `python scripts/validate.py` runs the section-7 honesty check: walk-forward (train 3y / test 6m),
  +-20% stability, Deflated Sharpe over every variant ever tried; saves `reports/validation-<date>.md`.
- History comes from data.binance.vision (every zip checked against its SHA-256) plus the
  public REST API for the latest weeks, stored in `data/market.sqlite`. Re-runs fetch only what's new.
- The engine (`src/cryptobot/backtest/engine.py`) works day by day: funding at real settlement
  times on mark price, next-day-open fills with slippage + taker fee, stops on the mark-price
  range (gap fills at the bar extreme, as the spec requires), live lot sizes (0.001 BTC/ETH)
  and minimum order values, and account liquidation at maintenance margin.

## The trading plan: two sleeves (chosen 2026-10-30)

Two sleeves on separate capital (live: separate Binance sub-accounts, because one-way mode
can't hold a long and a short on the same coin in one account):

| Sleeve | Config | What it does | Capital |
|---|---|---|---|
| Core | `config/core_big.yaml` | daily Donchian trend ensemble, 6 coins, longs + half-size shorts | 60% |
| Strongest vs weakest | `config/xs.yaml` | every Sunday: long the 4 strongest, short the 4 weakest of the 30 most-traded coins | 40% |

Both use `risk_profile: aggressive` (the owner's budget): kill switch at -35% drawdown, leverage
still capped at 3x. Hard limits per profile: `HARD_LIMITS_BY_PROFILE` in `src/cryptobot/risk/portfolio.py`.

Tested and rejected, kept for the record: the 4h satellite (`config/satellite.yaml`, mean reversion hit
the kill switch) and the open-interest crowding filter (off by default, no improvement).
`config/strategy.yaml` is the cautious spec version; `config/core.yaml` the first aggressive core.

```powershell
python scripts/download_history.py SOLUSDT BNBUSDT XRPUSDT DOGEUSDT --extended   # core data
python scripts/download_universe.py        # every USDT perpetual, delisted ones included
python scripts/download_metrics.py         # archived open interest + long/short ratios (2020-09 on)
python scripts/validate_core_big.py        # gates for the core
python scripts/validate_xs.py              # gates for the sleeve and the 60/40 mix
```

Reports go to `reports/`. Every run is added to `data/variants_log.csv` and counted in the Deflated
Sharpe, so fix the rules first and run a test once.

### Level 1: paper trading

With `level: 1` the daily job replays both sleeves from `start_date` in `config/paper.yaml` through
the validation backtester, on real prices, with no orders. Would-be fills go into the trade log as
`paper-core` / `paper-xs`, so `trades`, `stats` and the Telegram commands show them separately.
A recorded fill that changes on a later replay raises a warning in the daily report.

## Going live (later - order placement is not built yet)

All of these are required; any one missing and the bot refuses to start:

1. `config/config.yaml`: `environment: live` and `live_trading: true`
2. `.env`: `BINANCE_FUTURES_LIVE_API_KEY` + `BINANCE_FUTURES_LIVE_PRIVATE_KEY_PATH`
   (separate key pair, Futures only, withdrawals disabled, IP-restricted)
3. In the terminal, for that session only (never in `.env`):
   ```powershell
   $env:CRYPTOBOT_CONFIRM_LIVE = "I_UNDERSTAND_REAL_MONEY"
   python -m cryptobot
   ```
   Closing the terminal removes it.

## Layout

```
config/config.yaml        settings: mode, environment, pairs, leverage, risk limits
src/cryptobot/
  __main__.py             entry point (python -m cryptobot)
  config.py               loads YAML + .env, validates, live guard, key loading
  models.py               shared data types (Signal, OrderRequest, Position, Fill ...)
  logging_setup.py        console + rotating file logs, masks secrets
  indicators.py           EMA / RSI / ATR via pandas-ta
  exchange/
    futures_client.py     USDⓈ-M futures adapter around the official Binance SDK
    filters.py            exchange rules + Decimal rounding (tick, step, min notional)
    ccxt_client.py, market_data.py, paper_broker.py   spot (parked)
  data/
    market_stream.py      WebSocket klines + mark price, auto-reconnect
    user_stream.py        user data stream (listenKey), events -> SQLite
    candles.py            REST candles (spot)
  strategies/             Strategy base class; strategies only emit Signals
  risk/                   RiskManager - approves/rejects every order
  execution/              order placement (next steps)
  portfolio/              balances, positions, PnL
  storage/                SQLite: orders, fills, stream events, funding, risk events
  notifications/          Telegram (stub)
  modes/                  futures (current), spot (parked)
  backtest/               backtester (later)
tests/                    pytest tests
logs/, data/              created at runtime, git-ignored
```

### How data flows

```
market stream (closed candles, mark price) -> Strategy -> Signal -> sized OrderRequest
     -> RiskManager.review(order, account snapshot) -> approved?
     -> execution/ (Decimal rounding, clientOrderId, algo stop-loss) -> futures_client
     -> user stream confirms fills/stops -> storage/ (SQLite) ; notifications/ alerts
```

## Spot (parked)

Set `mode: spot` with `environment: paper` or `testnet` (keys from
<https://testnet.binance.vision>: `BINANCE_SPOT_TESTNET_API_KEY` / `_API_SECRET`).
`python -m cryptobot` then prints candles with indicators and balances.
Testnet spot prices are thin and spiky; `market_data: live` uses real prices for candles.

## Troubleshooting

- **`Account is in HEDGE position mode`** / **`Multi-Assets mode`** - change it in the
  Binance Futures settings (demo or live), then rerun.
- **`-2015 Invalid API-key, IP, or permissions`** - wrong key for the environment
  (demo keys only work on demo), Futures permission missing, or your IP changed.
- **`DLL load failed ... An Application Control policy has blocked this file`** -
  Windows Smart App Control blocked a compiled package file.
- **`Timestamp for this request is outside of the recvWindow`** - sync your
  Windows clock (Settings → Time & language → Sync now).
