# Bot Roadmap & Principles — From Basic to Advanced, With the Bot as Advisor

Read with `CLAUDE.md`, `binance-futures-bot-reference.md` and `strategy-risk-spec.md`.
This file explains **what matters most**, **how the bot grows in levels**, and **how the bot
gives tips to the owner** (not the other way around).

> Information only, not financial advice. Most leveraged retail traders lose money.
> Only trade money you can afford to lose completely.

---

## Part 1 — What matters most (ranked)

The research is clear: people lose in leveraged crypto mainly through **too much leverage,
high costs and forced exits (liquidations)** — not through picking the wrong coin or indicator.

- 70–97% of day traders lose money over time.
- Hyperliquid study (Jia et al., 2026, full on-chain record Mar 2025–Jun 2026): manual retail
  traders lost $1.11B, market makers gained $208M, algorithmic traders roughly broke even.
  The three channels: paying for immediacy, forced exits, fees.
- 2025: over $154B of forced liquidations across perpetual futures; retail leverage often >10x,
  some 50–100x.
- Beginner bots fail mostly from vague strategies, overfitting, loose risk controls and
  insecure API keys — not from the bot itself.

| # | Area | Rule for this project |
|---|---|---|
| 0 | **Money you can lose** | Decide the total amount and the loss that makes you stop *before* starting |
| 1 | **Risk management** | Leverage 2x (cap 3x), 0.5% risk/trade, stops always on exchange, daily/weekly/drawdown limits |
| 2 | **Costs** | Few trades, limit orders where possible, fees + slippage + funding in every backtest |
| 3 | **Honest testing** | Walk-forward, out-of-sample, ±20% parameter check, weeks of demo |
| 4 | **Operations & security** | Bug-free orders, reconciliation, IP-restricted keys, no withdrawal permission |
| 5 | **Discipline** | Never override after losses, never raise size after wins, never disable stops |
| 6 | **Strategy** | Simple trend following first; complexity only when earned |
| 7 | **Market regime** | Adjust size by regime instead of hunting for a perfect strategy |
| 8 | **Portfolio** | BTC/ETH/SOL move together — cap *total* risk, not just per trade |
| 9 | **Coin pick** | Liquidity first (tight spreads, deep books). BTC, ETH → SOL → others |
| 10 | **Country / tax / legal** | Israel: crypto is an asset, 25% capital gains (more if business). Log everything. Check product availability |
| 11 | **Exchange risk** | ADL, outages in crashes, funds held by exchange → keep most capital off the futures wallet |
| 12 | **Profit** | An outcome, not an input. Expect modest returns and long flat periods |
| 13 | **Trends / social / hype** | Noise for a rule-based bot. News trading is out of scope for now |
| 14 | **Ethics** | Trading own money honestly is fine. Don't sell signals, run copy-trading or show unproven results to others |

---

## Part 2 — Tactics: what's allowed when

### ✅ Beginner-friendly (allowed early)
| Tactic | Why | Watch out |
|---|---|---|
| Trend following (MA cross / Donchian, 4h–daily) | Simple, few trades, best evidence | Long flat/losing stretches in chop |
| Simple breakout + trailing stop | Same family, easy to code | False breakouts |
| Limit-order / TWAP-style execution | Saves fees | Unfilled orders need fallback |
| Plain DCA / rebalancing on spot (optional side project) | Can't be liquidated | Not a profit strategy alone |

### ⚠️ Later levels only
| Tactic | Condition |
|---|---|
| Mean reversion with regime filter | After a trend bot is stable live |
| Spot grid (no leverage) | After order management is solid |
| Funding / OI as filters | Added on top of a working trend bot |
| 4h trend sleeve | Only if it passes validation and correlation < 0.7 with daily |

### ❌ Ruled out (not for a first self-built bot)
Martingale / safety-order DCA · futures grid with leverage · QFL / buy-the-dip · scalping ·
market making (pure, cross-exchange, Avellaneda-Stoikov) · cross-exchange & triangular
arbitrage · funding-rate arbitrage (spot + perp) · pairs / stat-arb · AI/ML prediction bots ·
third-party signal bots · copy trading.

Reason: blow-up risk, fees, professional infrastructure needed, or results that can't be verified.

---

## Part 3 — Levels: the bot grows step by step

Each level must pass its **exit gate** before the next one starts. The bot never skips a level
on its own.

### Level 0 — Observer (no orders)
- Collect data: klines, mark price, funding, OI and `/futures/data` snapshots (30-day limit → store daily).
- Compute indicators and regime (BTC vs 200d SMA, efficiency ratio/ADX, vol percentile, funding z-score).
- Daily Telegram report (see Part 4).
- **Exit gate:** runs 2 weeks without crashes or data gaps.

### Level 1 — Paper signals
- Core daily Donchian ensemble produces signals and *would-be* trades with full risk sizing.
- Since 2026-10-30 two sleeves run on paper: `config/core_big.yaml` and `config/xs.yaml` (`src/cryptobot/paper/runner.py`), at the owner's aggressive risk budget.
- Logged to SQLite as paper trades with fees + funding.
- **Exit gate:** backtest passes `strategy-risk-spec.md` section 7; paper results behave like the backtest.

### Level 2 — Demo execution
- Same code places orders on Binance demo: entries, algo stops, reconciliation, circuit breakers.
- **Exit gate:** 4–8 weeks, zero unprotected positions, zero duplicate orders, all alerts working.

### Level 3 — Live, minimum risk
- BTCUSDT + ETHUSDT, r = 0.25%, leverage 2x, live key IP-restricted on VPS.
- **Exit gate:** 30 trades with no operational incident; slippage/funding close to model.

### Level 4 — Live, standard risk
- r = 0.5%, add SOLUSDT, regime overlay active.
- **Exit gate:** 3–6 months, drawdown within backtest expectations.

### Level 5 — Second strategy
- Add the 4h trend sleeve or mean reversion with regime filter — **paper first, then demo**,
  repeating Levels 1–3 for that strategy only.

### Level 6 — Advanced (optional, much later)
- Satellite large caps (BNB, XRP, DOGE) at ≤ 0.5× size.
- Funding/OI crowding filters tuned with walk-forward.
- Only after a full year of live data.

---

## Part 4 — The bot as advisor: it gives tips to the owner

The bot **explains, warns and suggests**. The owner **decides**. The bot never changes risk
limits, leverage, strategy or level by itself.

### What the bot may do on its own
- Follow the coded strategy and risk rules exactly.
- Tighten risk automatically when rules say so (circuit breakers, drawdown scaling, funding limits).

### What the bot may only *suggest* (owner approves in Telegram or by editing config)
- Raising risk, leverage, or adding a symbol
- Moving to the next level
- Changing parameters or enabling a new strategy
- Turning a strategy back on after a stop

### Daily report (Telegram, once a day after the daily close)
1. **Market:** regime per symbol (trend / chop / high vol), BTC vs 200d SMA, funding level, crowding warnings.
2. **Positions:** open trades, distance to stop and liquidation, unrealized P&L, funding paid.
3. **Risk usage:** heat used vs limit, daily/weekly loss used, drawdown from high.
4. **What the bot did and why:** each entry/exit/skip with the rule that triggered it, in plain language.
5. **Tip of the day:** one short, data-based observation (see tip rules).

### Weekly review (Telegram + saved as markdown in `reports/`)
- Performance vs backtest expectation, win rate, average win/loss, fees and funding as % of gross P&L.
- Slippage vs model.
- Level-gate progress ("Level 2: 19 of 30 clean days").
- Up to 3 suggestions, each with evidence and a clear yes/no question for the owner.

### Tip rules (so tips are useful, not noise)
- Every tip is based on the bot's own data, with numbers ("Funding on ETH longs is 0.04%/8h = ~44%/yr; rule says halve above 30%").
- Tips teach a concept the first time it appears ("What is funding?") — short, then link to the docs section.
- Never recommend more risk because of recent wins.
- Never give tips based on news, social media or price predictions.
- If live results drift from the backtest, say so plainly.
- Maximum one tip per day; weekly suggestions max 3.

### Example tips
- "3 losing trades in a row is normal for trend following: backtest shows streaks up to 8. No change suggested."
- "ETH and BTC correlation is 0.82 this month. Your cluster risk cap is doing its job — both positions together use 1.1% of 1.25%."
- "Demo stop fills were on average 0.06% worse than trigger. Model assumes 0.03%. Suggest raising slippage in backtest to 0.06%? (yes/no)"
- "Chop regime for 12 days (ER 0.11). Trend sleeve running at 0.5× as designed. Expect few or losing trades until ER rises above 0.3."

---

## Part 5 — Owner's habits

- Check the daily report; answer suggestions; don't trade manually on the bot's account.
- Monthly: review costs, stop-fill quality, and whether you're still within your loss budget.
- Never paste keys anywhere. Rotate keys periodically.
- Keep the trade log for tax; get an accountant before scaling.
- If the drawdown stop (−20%) hits: stop, review, and only restart after understanding why.

---

## Part 6 — Prompt for Claude Code (advisor + levels)

```text
Read CLAUDE.md, binance-futures-bot-reference.md, strategy-risk-spec.md and
bot-roadmap-and-principles.md. Plan (don't code yet) how to add:
1. A `level` setting in config (0–6) that controls what the bot is allowed to do:
   level 0 = data only, 1 = paper trades, 2 = demo orders, 3+ = live with the risk
   settings for that level. The bot must refuse to run actions above its level.
2. An advisor module (src/advisor/) that builds the daily Telegram report and weekly
   markdown review described in Part 4, using only the bot's own data, and stores
   suggestions with a pending/approved/rejected status in SQLite.
3. Approval flow: suggestions that increase risk, change parameters or move a level
   require my explicit approval; risk-reducing rules still apply automatically.
Show the plan and wait for approval. Start implementation at Level 0 only.
```

---

## Part 7 — Sources

- Market microstructure of perpetual futures (Jia et al., 2026): https://drops.dagstuhl.de/entities/document/10.4230/LIPIcs.AFT.2026.36
- 2025 futures mistakes & liquidations: https://finance.yahoo.com/news/3-crypto-futures-trading-mistakes-170000293.html
- Retail perps loss rates: https://www.kucoin.com/news/flash/us-retail-traders-flock-to-high-risk-perpetual-futures-contracts
- BIS — most retail Bitcoin investors lost money: https://www.investmentexecutive.com/?p=13408
- Beginner bot mistakes: https://coinbureau.com/guides/crypto-trading-bot-mistakes-to-avoid
- Binance built-in bot types: https://www.cryptoninjas.net/exchange/binance-trading-bot/
- Hummingbot (market making, for reference only): https://www.hummingbot.org/strategies/cross-exchange-market-making
- Strategy evidence and full source list: see `strategy-risk-spec.md` section 11
