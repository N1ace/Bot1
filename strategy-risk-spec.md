# Strategy & Risk Specification — Binance USDⓈ-M Futures Bot

Implementation spec for Claude Code. Read together with `CLAUDE.md` and
`binance-futures-bot-reference.md`. Source: research report of 2026-10-28.

> Information only, not financial advice. Published backtests degrade live; plan for a
> live Sharpe of roughly 0.5–1.0 and drawdowns of 20–30%. News/event trading is out of scope.

---

## 1. Strategy choice (why)

| Strategy | Verdict for this bot |
|---|---|
| **Daily Donchian / time-series-momentum ensemble, vol-targeted** | ✅ **Core strategy.** Strongest evidence net of costs (Zarattini–Pagani–Barbon; Han, Kang & Ryu 2024). Few trades → low fees |
| 4h EMA / Donchian trend with chandelier exit | ⚠️ Optional second sleeve, only if it passes validation gates |
| Intraday trend / scalping (15m–1h) | ❌ Fees kill it (~0.14–0.20% per round trip) |
| Mean reversion (Bollinger/RSI) | ❌ Weak single-asset evidence, big losers when trends start |
| Funding carry (short perp + long spot) | ❌ Not directional, needs spot leg, edge shrinking since 2024 |
| Funding / OI / liquidation as signals | ⚠️ Use as **risk filters only**, not entries |
| Cross-sectional momentum among large caps | ❌ Weak evidence, coins too correlated |
| Grid / DCA bots | ❌ Mostly marketing; accumulates losing inventory in crashes |
| BTC–ETH pairs / stat-arb | ❌ Fees on four legs erase the edge |

Reference result (backtest, BTC, net of 10 bps costs, **funding not included**):
net CAGR 30%, Sharpe 1.56, max DD 19%. Holding longs at baseline funding costs ~11%/yr
on notional → **funding must be in our backtest.**

---

## 2. Core strategy rules — daily Donchian ensemble

- **Universe:** BTCUSDT, ETHUSDT first; add SOLUSDT later; other large caps (BNB, XRP, DOGE) only at ≤0.5× size.
- **Timeframe:** daily bars, signals computed at daily close (00:00 UTC), orders on the next bar.
- **Lookbacks:** `L ∈ {10, 20, 30, 60, 90, 150, 250}` → 7 sub-signals per symbol.
- **Long entry (per sub-signal):** `close > max(close over previous L days)`.
- **Short entry (optional, test separately at 0.5× weight):** `close < min(close over previous L days)`.
  Evidence warns losers often rebound, so shorts are secondary.
- **Exit (per sub-signal):** trailing stop at the Donchian channel midpoint
  `(max(L) + min(L)) / 2`, ratcheting only in the trade's favour.
- **Aggregation:** each sub-signal gets 1/7 of the symbol's vol budget; net them into one
  target position per symbol, then apply risk layers 1–3.
- **Rebalance:** only when target size differs from current size by more than 20%.
- **Catastrophe stop:** exchange-side STOP_MARKET at `3 × ATR(20)` from entry, always live.

### Optional sleeve — 4h trend (0–30% of risk budget)
EMA(20/100) cross or 4h Donchian(20/55) breakout, chandelier exit
`highest high − 3 × ATR(14)`. Gated by the trend/chop filter. Add only if its correlation to
the daily sleeve is < 0.7 and it passes the validation gates in section 7.

---

## 3. Risk model (6 layers)

### Layer 1 — Per-trade sizing
```
stop_dist  = k_atr × ATR(20)                      # k_atr default 3.0 (test 2–4)
             (or Donchian midpoint, whichever is tighter at entry)
qty_risk   = (equity × r) / (stop_dist × (1 + slip_buffer))
             r default 0.005; slip_buffer 0.25 BTC/ETH, 0.5 alts
qty_vol    = (equity × vol_target_asset) / (realized_vol_ann × price)
             vol_target_asset default 0.15; realized vol over 30–90 days
qty        = min(qty_risk, qty_vol) → round DOWN to stepSize
reject if qty × price < MIN_NOTIONAL
```
No full Kelly. If ever computed, cap at 0.25× Kelly.

### Layer 2 — Leverage & liquidation
- Isolated leverage per symbol: default 2x, hard cap 3x.
- Account effective leverage `Σ|notional| / equity`: default ≤ 1.5x, hard cap 2.5x.
- Liquidation distance ≈ `1/L − MMR` (get MMR from `leverageBracket`, don't hard-code).
- Rule: `stop_dist ≤ 0.33 × liquidation_distance`.
- Stops trigger on `workingType = MARK_PRICE`.

### Layer 3 — Portfolio limits
| Parameter | Default | Hard limit |
|---|---|---|
| Max concurrent positions | 4 | 6 |
| Total open risk ("heat") | 2.0% of equity | 3.0% |
| BTC/ETH/SOL cluster open risk | 1.25% | 1.5% |
| Other large caps per name | 0.5% risk, ≤ 0.4× equity notional | — |
| Same-direction net notional | 1.25× equity | 2× equity |

BTC–ETH ≈ 0.76, BTC–SOL ≈ 0.75, ETH–SOL ≈ 0.68 (daily, 2024–2025). Cluster risk:
`sqrt(Σ_i Σ_j ρ_ij · r_i · r_j)` with ρ from 60 days, floored at 0.7.

### Layer 4 — Circuit breakers
- **Daily loss −2%** (realized + unrealized at mark, from start of UTC day): block new entries until 00:00 UTC. Don't auto-close.
- **Weekly loss −4%:** block entries for the rest of the week.
- **Drawdown from equity high:** −10% → r × 0.5; −15% → r × 0.25; −20% → flatten everything, stop bot, Telegram alert, manual review.
  Step back up only after recovering half the drawdown.
- **Losing streak:** after 6 consecutive losers, r × 0.5 for the next 10 trades.

### Layer 5 — Funding
- Poll `/fapi/v1/premiumIndex` and `/fapi/v1/fundingInfo`; respect each symbol's actual
  settlement interval (many are 4h; capped symbols can switch to 1h).
- Funding against the position annualized > 30% (≈ 0.027%/8h) → halve position.
  > 60% → close, unless trend signal at full strength.
- Log every funding payment per trade in SQLite; include in P&L.

### Layer 6 — Operations
- Every position has an exchange-side STOP_MARKET via `POST /fapi/v1/algoOrder`
  (`algoType=CONDITIONAL`, `closePosition=true` or reduceOnly qty, `workingType=MARK_PRICE`,
  `priceProtect=true`). `/fapi/v1/order` returns −4120 for stop types since 2025-12-09.
  In the SDK use the algo-order method, not `new_order()`.
- Reconcile every 60s and on startup: every open position must have a live algo stop.
  Missing → place it; if that fails → market-close and Telegram alert.
- Stale data (klines/mark price older than 2 intervals) or WebSocket down > 60s →
  block entries. **Never cancel stops on disconnect.**
- Assume stops can fill far past the trigger in crashes (10 Oct 2025: ~$19B liquidated,
  many alts −33% in ~25 min, ADL hit profitable positions). Keep alt sizes small and
  most capital outside the futures wallet.

---

## 4. Regime overlay (adjust size, don't switch strategies)

| Signal | Computation | Action |
|---|---|---|
| Market trend state | BTC close vs. 200-day SMA + sign of SMA 20-day slope | Above & rising: longs 1×, shorts 0.5×. Below & falling: longs 0.5×, shorts 1×. Mixed: both 0.5× |
| Trend vs. chop | Efficiency ratio (20d) or ADX(14) | ER > 0.3 or ADX > 25: full size. ER < 0.15 or ADX < 18: trend sleeve 0.5× |
| Volatility | 30-day realized vol percentile vs. trailing 1 year | > 90th pct: r × 0.5 (vol targeting also shrinks size). < 10th: no extra leverage |
| Crowding (context) | Funding z-score (90d), OI change vs. price | Funding z > 2 and OI rising fast: block new longs, tighten trailing stops |
| Hurst / HMM | — | Research only, not in live logic |

**Turn a sleeve off** only when trailing-12-month live Sharpe < −0.5 **and** drawdown
> 1.5× worst backtest drawdown. Either alone → review only.

---

## 5. Costs to model

| Item | Value |
|---|---|
| Taker fee | 0.05% of notional per side |
| Maker fee | 0.02% (use post-only entries where possible) |
| Slippage | 2–5 bps BTC/ETH, 5–10 bps alts |
| Round trip budget | 0.14–0.20% |
| Funding | Actual history from `fundingRate`, at real settlement times/intervals |

(verify current fee tier on Binance before go-live)

---

## 6. Data

- **History:** data.binance.vision `futures/um` — daily/monthly klines, mark/index/premium
  klines, fundingRate, `metrics` (5-min OI, long/short ratios, taker buy/sell).
  Repo: https://github.com/binance/binance-public-data
- **Live:** `/fapi/v1/klines`, `/fapi/v1/markPriceKlines`, `/fapi/v1/fundingRate`,
  `/fapi/v1/premiumIndex`, `/fapi/v1/fundingInfo`, `/fapi/v1/openInterest`,
  `/futures/data/openInterestHist`, `topLongShortPositionRatio`,
  `globalLongShortAccountRatio`, `takerlongshortRatio`.
- `/futures/data/*` endpoints keep **only the last 30 days** → store them in SQLite from day one.

### Backtest hygiene
- Signal at bar close, fill at next bar open + slippage (no look-ahead).
- Funding charged at actual settlement times.
- Gap bars: stop fills at the worse of trigger price and bar extreme.
- Point-in-time universe (only symbols listed and liquid on that date).

---

## 7. Validation gates (before any real money)

1. Walk-forward 2020–2026: train 3 years / test 6 months, rolled every 6 months.
2. ≥ 100 trades total.
3. Net Sharpe > 0.8, max DD < 25% at target vol.
4. Stable when every parameter moves ±20%.
5. Deflated Sharpe Ratio probability > 0.95 across all variants tried
   (Bailey & López de Prado, 2014). Log every variant tested.
6. Then 4–8 weeks on demo — **plumbing and order logic only**, not performance proof.

---

## 8. Build order

1. Data downloader + storage (history from data.binance.vision, live `/futures/data` snapshots)
2. Event-driven backtester with fees, slippage, funding, next-bar fills, gap-aware stops
3. Donchian ensemble strategy as a pure function (bars in → target position out)
4. Risk layers 1–4 as a separate module with pytest coverage
5. Funding layer + regime overlay
6. Walk-forward + DSR report
7. Wire into live engine (already: streams, user data, algo stops, reconciliation)
8. Demo run → live ramp

### Live ramp
- Start `r = 0.0025`, BTC + ETH only.
- After 30 trades with no operational incidents → `r = 0.005`, add SOL.
- Other large caps only at ≤ 0.5× size.
- Monthly review: slippage vs. model, funding paid, stop-fill quality.

---

## 9. Default config (`config/strategy.yaml`)

```yaml
strategy:
  name: donchian_ensemble_daily
  timeframe: 1d
  lookbacks: [10, 20, 30, 60, 90, 150, 250]
  allow_shorts: false          # test separately at 0.5x weight
  short_weight: 0.5
  rebalance_threshold: 0.20
  atr_n: 20
  k_atr: 3.0

symbols:
  core: [BTCUSDT, ETHUSDT]     # add SOLUSDT after live ramp step 2
  satellite: []                # BNBUSDT, XRPUSDT, DOGEUSDT later at 0.5x
  satellite_size_mult: 0.5

risk:
  risk_per_trade: 0.005        # start live at 0.0025
  vol_target_portfolio: 0.20
  vol_target_asset: 0.15
  slip_buffer: {BTCUSDT: 0.25, ETHUSDT: 0.25, default: 0.5}
  isolated_leverage: 2
  max_leverage: 3
  max_eff_leverage: 1.5
  max_eff_leverage_hard: 2.5
  max_positions: 4
  max_heat: 0.02
  cluster_heat: 0.0125
  cluster: [BTCUSDT, ETHUSDT, SOLUSDT]
  corr_floor: 0.7
  stop_to_liq_max: 0.33
  daily_loss: 0.02
  weekly_loss: 0.04
  dd_steps: {0.10: 0.5, 0.15: 0.25, 0.20: stop}
  losing_streak: {count: 6, mult: 0.5, trades: 10}

funding:
  halve_above_ann: 0.30
  exit_above_ann: 0.60

regime:
  btc_sma: 200
  sma_slope_days: 20
  er_n: 20
  er_trend: 0.30
  er_chop: 0.15
  adx_n: 14
  adx_trend: 25
  adx_chop: 18
  vol_pct_window_days: 365
  vol_high_pct: 0.90
  vol_high_mult: 0.5
  funding_z_block: 2.0

execution:
  working_type: MARK_PRICE
  price_protect: true
  entry_order: post_only_limit_then_market   # fall back to market after timeout
  reconcile_seconds: 60
  stale_data_intervals: 2
  ws_down_block_seconds: 60

costs:
  taker: 0.0005
  maker: 0.0002
  slippage_bps: {BTCUSDT: 3, ETHUSDT: 3, default: 8}
```

---

## 10. Prompt for Claude Code

```text
Read CLAUDE.md, binance-futures-bot-reference.md and strategy-risk-spec.md.
Plan steps 1–2 of section 8 (data downloader + backtester) and wait for my approval.
Requirements: data from data.binance.vision futures/um for BTCUSDT and ETHUSDT daily
klines and fundingRate since 2020; event-driven backtester with taker/maker fees,
slippage, funding at real settlement times, next-bar fills, gap-aware stops; pytest tests
for fee/funding/fill math. No live orders. Report results honestly.
```

Then, after it works: steps 3–4 (strategy + risk module), reviewed by the risk-reviewer agent.

---

## 11. Key sources

- Zarattini, Pagani & Barbon — Catching Crypto Trends: https://concretumgroup.com/catching-crypto-trends-a-tactical-approach-for-bitcoin-and-altcoins/
- Han, Kang & Ryu (2024) — crypto momentum with costs/liquidations: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4675565
- CXO Advisory summary of crypto trend following: https://www.cxoadvisory.com/technical-trading/crypto-asset-trend-following-strategies/
- Concretum — intraday BTC trend & seasonality: https://concretumgroup.com/seasonality-in-bitcoin-intraday-trend-trading/
- Regime-switching RL study (no OOS gain): https://link.springer.com/article/10.1007/s42521-024-00123-2
- Binance New Algo Order: https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/New-Algo-Order
- Binance derivatives changelog: https://developers.binance.com/docs/derivatives/change-log
- Binance funding rate info: https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-Info
- Binance funding rate updates: https://www.binance.com/en/support/announcement/important-updates-on-funding-rates-of-usd%E2%93%A2-m-perpetual-contracts-98d6b24d3e5c4f84a8ed04087997d8d0
- Historical data: https://github.com/binance/binance-public-data
- Bloomberg on 2025–26 crypto trend performance: https://www.bloomberg.com/news/articles/2026-02-27/crypto-trend-following-trade-finds-relief-after-sharp-selloff
