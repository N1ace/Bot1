# Binance USDⓈ-M Futures Bot — Project Reference

A single place for the decisions, settings and official sources behind this project.
Keep it in the repo root next to `CLAUDE.md` and point Claude Code at it.

> Information only, not financial advice. Leverage can lose money fast. Verify anything
> marked **(verify)** before relying on it — Binance changes URLs, limits and fees often.

---

## 0. Current status (updated 2026-10-28)

| Item | Status |
|---|---|
| Trading product | USDⓈ-M futures, **demo environment** |
| Demo API key | ✅ Created (Ed25519, self-generated, no passphrase for now) |
| `.env` | ✅ Configured, demo connection working |
| Live API key | ⏸ Not created yet — only after the demo phase passes |
| Prompt A (startup, market stream, user stream) | ✅ Built and tested on demo 2026-10-28 |
| Current phase | Roadmap step 4→6: reconciliation loop, risk manager sizing |
| Prompt B (demo test trade) | ✅ 2026-10-28: entry, algo stop (MARK_PRICE, priceProtect) confirmed, close, verify — OK |
| Orders placed so far | Demo only: 1 test round trip (0.0007 BTC), net −0.04 USDT |
| Live account size (planned) | ~20,000 USD |
| Spec steps 1–2 (data + backtester) | ✅ 2026-10-28: BTC/ETH 2020-01→2026-10 stored; engine sanity-checked |

**Next:** risk manager (0.5% risk sizing, liquidation buffer), the risk-reviewer, then Prompt B
(first demo test trade).

**Strategy & risk:** see `strategy-risk-spec.md` (core: daily Donchian ensemble,
vol-targeted, 6-layer risk model, regime overlay, validation gates, default config).

---

## 1. Decisions so far

| Topic | Decision |
|---|---|
| Exchange | Binance |
| Product | USDⓈ-M perpetual futures (switched from spot margin) |
| Pairs | BTCUSDT, ETHUSDT first; maybe SOL/BNB/XRP later |
| Margin mode | Isolated |
| Position mode | One-way; asset mode Single-Asset |
| Leverage | Default 2x, hard cap 3x in code |
| Sizing | Risk per trade (default 0.5% of balance) ÷ stop distance — leverage never sizes |
| Library | Official Python SDK `binance-sdk-derivatives-trading-usds-futures`, wrapped in our own adapter. CCXT only as fallback |
| Keys | Ed25519 ("Self-generated"), separate pairs for demo and live |
| Environment | Demo first, live only small and after weeks of demo |
| Dev tools | VS Code + Claude Code, subagents `bot-developer` and `risk-reviewer` |
| AI trading (MCP) | Not used by the bot. Optional for read-only questions, separate session, tiny funds |
| Hosting (live) | VPS with static IP |

---

## 2. Setup checklist

### Binance account
- [ ] Open Futures account (app → Derivatives → Futures, quiz) **before** creating the key
- [ ] Futures settings: One-way mode, Single-Asset mode
- [ ] Confirm futures are available for your verified residence (Israel) in the app **(verify)**

### Ed25519 key pair
- [ ] Download Key Generator desktop app from GitHub Releases, verify checksum
      (Windows: `Get-FileHash .\<file>.exe -Algorithm SHA256`)
- [ ] Key type Ed25519 → Generate → **Save Pair** outside the project folder,
      e.g. `C:\Users\<you>\binance-keys\futures-demo\`
- [ ] Live key: separate pair, with a passphrase
- [ ] Never paste the private key anywhere (chat, Claude Code, screenshots)

### API key (API Management → Create API → Self-generated)
| Permission | Setting |
|---|---|
| Enable Reading | ✅ |
| Enable Futures | ✅ |
| Spot & Margin, Margin Loan, Universal Transfer, Prediction, FIX | ❌ |
| Enable Withdrawals / Alpha Withdrawals | ❌ never |
| Symbol whitelist | Optional (BTCUSDT, ETHUSDT) |
| IP access | Trusted IPs only |

- [ ] Tick **Default Security Controls** (revokes any trading key without an IP restriction)
- [ ] Dynamic home IP: whitelist current IP (`Invoke-RestMethod https://api.ipify.org`),
      update when it changes; check IPv6 (`https://api64.ipify.org`) if requests fail
- [ ] Before live: VPS static IP whitelisted, home IP removed

### Project files
```env
# .env  (listed in .gitignore) - actual names used by the code.
# The environment is chosen in config/config.yaml (environment: demo), not in .env.
# BINANCE_API_KEY / BINANCE_API_SECRET are taken by the Freqtrade setup, so the bot uses
# per-account, per-environment names; demo and live keys can never be mixed up.
BINANCE_FUTURES_DEMO_API_KEY=...
BINANCE_FUTURES_DEMO_PRIVATE_KEY=<one-line key body>          # or _PRIVATE_KEY_PATH=
# BINANCE_FUTURES_LIVE_API_KEY=...
# BINANCE_FUTURES_LIVE_PRIVATE_KEY_PATH=C:\Users\<you>\.secrets\futures_live_ed25519.txt
# BINANCE_FUTURES_LIVE_PRIVATE_KEY_PASSPHRASE=...           (live key only)
```
```powershell
pip install binance-sdk-derivatives-trading-usds-futures
pip freeze > requirements.txt    # pin versions; the SDK's major version changes often
```

---

## 3. Environments (put all URLs in config, test each on day one)

| | Live | Demo |
|---|---|---|
| REST | `https://fapi.binance.com` | `https://demo-fapi.binance.com` |
| Market streams | `wss://fstream.binance.com/market/stream?streams=...` | `wss://demo-fstream.binance.com/market/stream?streams=...` |
| User data stream | `wss://fstream.binance.com/private/ws/<listenKey>` | `wss://demo-fstream.binance.com/private/ws/<listenKey>` |
| WebSocket API | `wss://ws-fapi.binance.com/ws-fapi/v1` | `wss://testnet.binancefuture.com/ws-fapi/v1` **(verify)** |

Since 2026-04-23 futures streams are routed by path: `/market` (klines, mark price, aggTrade),
`/public` (book ticker, depth), `/private` (user data). The old `/ws` and `/stream` paths no
longer deliver market data on live. REST, `/market` and `/private` were tested on demo
2026-10-28 (live `/market` also tested).

Demo is for testing the plumbing (orders, stops, reconnects), not for judging strategy
results — fills and liquidity are unrealistic.

---

## 4. Rules the code must follow

**Stops**
- Since 2025-12-09, STOP_MARKET / TAKE_PROFIT_MARKET / TRAILING_STOP_MARKET go to
  `POST /fapi/v1/algoOrder` with `algoType=CONDITIONAL`. `/fapi/v1/order` rejects them
  with `-4120 STOP_ORDER_SWITCH_ALGO`.
- Use `closePosition=true` or `reduceOnly=true`, `workingType=MARK_PRICE`.
- Place the stop right after entry fills; confirm via `ALGO_UPDATE`; if it fails, close the position.
- Track `algoId` / `clientAlgoId` separately from `orderId`.

**Orders**
- `Decimal` for all prices/quantities; round to tick size, step size, min notional from `exchangeInfo`.
- Unique `newClientOrderId` saved before sending.
- HTTP 503 "unknown status": query the order, do **not** resend.
- Validate with `POST /fapi/v1/order/test`.

**Data and account state**
- Prices from WebSockets (`@kline_<interval>`, `@markPrice`, `@bookTicker`); REST only for history and reconciliation.
- User data stream: listenKey valid 60 min, keep alive with PUT, recreate on `-1125`.
  Handle `ORDER_TRADE_UPDATE`, `ACCOUNT_UPDATE`, `ALGO_UPDATE`.
- Reconcile with REST (`positionRisk`, open orders, open algo orders) on startup and every few minutes.
- WebSocket API connections last 24h — build reconnect + resubscribe from day one.

**Rate limits (defaults — read live values from `exchangeInfo` and headers)**
- 2,400 request weight/min per IP; 1,200 orders/min; 300 orders/10s.
- Headers `X-MBX-USED-WEIGHT-*`, `X-MBX-ORDER-COUNT-*`.
- Repeated 429s → HTTP 418 IP ban (2 minutes to 3 days). Back off on 429.

**Risk**
- Isolated margin + leverage set per symbol at startup.
- Max open positions, max notional per symbol, daily loss limit → halt, max drawdown → kill switch.
- Stop must sit well inside the liquidation price.

**Security**
- No withdrawal or transfer code anywhere.
- Never read, print or log key material.
- Live trading only if config `environment: live` + `live_trading: true` AND env var
  `CRYPTOBOT_CONFIRM_LIVE=I_UNDERSTAND_REAL_MONEY` typed per terminal session (never in `.env`).

**Logging (also for Israeli tax)**
- Per fill: UTC time, symbol, side, qty, price, fee + fee asset, realized PnL, order IDs.
- Funding payments as separate rows.

---

## 5. Costs and strategy notes

- Regular-user USDⓈ-M fees ≈ 0.02% maker / 0.05% taker on **notional** **(verify current schedule)**.
  Taker round trip ≈ 0.10% of notional ≈ 0.30% of margin at 3x, before slippage.
- Funding every 8h; baseline 0.01% per interval ≈ 11%/year on a held long. Can spike higher.
- Implication: avoid scalping. Prefer fewer, larger-move trades on 1h–4h bars.
- Starting candidates: trend following (EMA cross / Donchian + ATR trailing stop),
  volatility breakout; mean reversion only with a regime filter; funding as a filter.
- Strategies are pure functions (data in, signal out) so the same code runs in backtest, demo and live.
- Backtest with fees, slippage (1–2 ticks on BTC/ETH), funding, mark price; out-of-sample / walk-forward.

---

## 6. Roadmap

1. Keys + security
2. Demo environment connection (balance, position mode, candles, set isolated + 2x)
3. Live market data via WebSocket
4. User data stream + reconciliation
5. Backtesting (data.binance.vision + funding history)
6. Risk manager
7. Execution engine (entries, algo stops, idempotency, error handling)
8. Demo forward test 4–8 weeks, Telegram monitoring, Docker on VPS
9. Live: BTCUSDT only, 1x–2x, small capital, at least a month
10. Later: second pair, spot mode, copy-trading lead portfolio

---

## 7. Israel notes

- Crypto taxed as an asset: 25% capital gains for individuals (activity at business level taxed as ordinary income). Ask an Israeli accountant before scaling.
- Depositing trading proceeds into Israeli banks can be difficult; locally licensed providers may help.

---

## 8. Official sources

### For Claude Code (fetch these instead of guessing)
- Docs index (LLM-friendly): https://developers.binance.com/en/docs/llms.txt
- Full docs in one file (very large — prefer the specific pages below): https://developers.binance.com/en/docs/llms-full.txt
- USDⓈ-M general info: https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info.md
- USDⓈ-M quick start: https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/quick-start.md
- USDⓈ-M user data streams: https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/user-data-streams.md
- USDⓈ-M WebSocket API info: https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-api-general-info.md
- USDⓈ-M error codes: https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/error-code.md
- USDⓈ-M changelog: https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/change-log.md
- API key types (Ed25519 / RSA / HMAC): https://developers.binance.com/en/docs/products/spot/faqs/api_key_types.md

### Binance developer portal
- API catalog: https://developers.binance.com/en/docs/catalog
- SDKs & tools overview: https://developers.binance.com/en/docs/sdks-tools/overview
- Python connector: https://developers.binance.com/en/docs/sdks-tools/connectors/python
- Asymmetric Key Generator: https://developers.binance.com/en/docs/sdks-tools/tools/asymmetric-key-generator
- Binance CLI (optional, Linux/macOS, not actively maintained): https://developers.binance.com/en/docs/sdks-tools/tools/binance-cli
- Agent Native overview: https://developers.binance.com/en/docs/agent-native/overview
- Binance MCP Server: https://developers.binance.com/en/docs/agent-native/mcp-server/agentic

### Code and packages
- Python SDK repo (examples per package): https://github.com/binance/binance-connector-python
- USDⓈ-M SDK on PyPI: https://pypi.org/project/binance-sdk-derivatives-trading-usds-futures/
- Key Generator releases: https://github.com/binance/asymmetric-key-generator/releases
- Historical market data: https://github.com/binance/binance-public-data
- Freqtrade (benchmark / cross-check only): https://www.freqtrade.io/en/stable/exchanges/

### Binance support articles
- Futures rate limits: https://www.binance.com/en/support/faq/rate-limits-on-binance-futures-281596e222414cdd9051664ea621cdc3
- Copy-trading lead API key (later): https://www.binance.com/en/support/faq/how-to-create-an-api-key-for-a-futures-lead-trading-portfolio-2bec848b904b422197ce121d0925f20b
- Fee schedule: https://www.binance.com/en/fee/trading

### Claude Code
- Overview: https://docs.claude.com/en/docs/claude-code/overview
- Install (Windows PowerShell): `irm https://claude.ai/install.ps1 | iex`

---

## 9. Next steps and Claude Code prompts

Build order (demo only, no strategy yet):
1. Startup routine: verify one-way mode, set isolated + leverage, cache `exchangeInfo` filters
2. Live prices: WebSocket 1h klines + mark price, auto-reconnect
3. Account updates: user data stream → SQLite
4. Risk manager: 0.5% risk sizing, max positions, daily loss limit, with tests
5. First manual demo test trade: tiny entry + exchange-side algo stop, verify, close
6. Strategy — only after 1–5 work

### Prompt A — steps 1–3 (no orders)
```text
Read CLAUDE.md and binance-futures-bot-reference.md. The demo connection works
(.env has BINANCE_ENV=demo, API key and private key path).

Plan, then wait for my approval, then build:

1. src/exchange/futures_client.py startup routine:
   - verify one-way position mode (stop with a clear error if not)
   - set marginType ISOLATED and leverage from config (default 2, hard cap 3) for
     BTCUSDT and ETHUSDT
   - load and cache exchangeInfo filters (tick size, step size, min notional),
     with Decimal rounding helpers + pytest tests
2. src/data/market_stream.py: WebSocket subscription to 1h klines and mark price for
   both symbols, auto-reconnect and resubscribe, log only closed candles
3. src/data/user_stream.py: user data stream (listenKey create, keepalive every
   ~30 min, recreate on -1125), handle ORDER_TRADE_UPDATE, ACCOUNT_UPDATE and
   ALGO_UPDATE, save every event to SQLite
4. A run script that does the startup routine and runs both streams for 10 minutes,
   printing a short summary.

Demo environment only. Place NO orders in this step. Never print or log key material.
After building, run the tests and the script, and report results honestly.
```

### Prompt B — first demo test trade (after Prompt A works)
```text
Use the risk-reviewer agent to review src/ before we place any order.
Then build scripts/demo_test_trade.py for DEMO ONLY: open the minimum-size
BTCUSDT long at market, immediately place a STOP_MARKET via POST /fapi/v1/algoOrder
(algoType=CONDITIONAL, closePosition=true, workingType=MARK_PRICE) 2% below entry,
confirm the stop via ALGO_UPDATE and openAlgoOrders, wait 30 seconds, then cancel
the stop and close the position. If placing the stop fails, close the position
immediately. Log everything to SQLite. Ask me before running it.
```

If something fails, share the error message (never keys) to debug it.
