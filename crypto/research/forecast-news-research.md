# Should the Bot Add Forecast and News Layers? Evidence and a Recommended Design (BTC/ETH/SOL, Binance USDⓈ-M)

**Yes, add a layer, but don't make it a forecast layer. Make it an event-risk layer.** Use scheduled-event dates, options-implied volatility and market-stress triggers to *cut* risk automatically. Send yearly, monthly and weekly price forecasts and news to the owner in a briefing, and give them no weight in sizing or signals. Published price forecasts have a very poor record. News is priced within minutes to an hour, which is far faster than a daily bot can act. What the bot *can* use is the timing of known risk events and fast stress signals: those cut gap risk without second-guessing the trend signal.

## TL;DR
- **Price forecasts at any horizon: report them, don't trade them.** Bank and analyst year-end BTC targets have missed by wide margins year after year. The median of nine end-2025 forecasts overshot by 56%, and Standard Chartered cut its 2026 target from $300k to $150k to $100k while BTC trades near $78k. Stock-to-flow missed its own 2021 tests by more than 50%. Prediction markets are well calibrated, but they price the same information the bot's trend signal already sees.
- **Big moves come from two kinds of event, and the bot should handle each differently.** Scheduled releases (CPI, FOMC, payrolls, tariff deadlines) have known timing. Their effect on BTC peaks within the first hour, and how strong it is changes over time: CPI surprises explained about 25% of BTC's first-hour return over 2020–26, and over 50% at the 2022–23 peak. Unscheduled shocks (COVID −37% in a day, FTX −22% in under a day, the 10 Oct 2025 cascade −14% with $19bn liquidated) produce most of the tail losses, and you cannot forecast them from news fast enough.
- **Recommended design:** (1) an economic calendar that blocks *new entries and adds* around CPI, FOMC and payrolls and checks that stops are live; (2) a stress detector driven by price, liquidations, DVOL, funding and stablecoin pegs that tightens risk automatically; (3) a venue and counterparty kill-switch; and (4) an LLM-written daily or weekly briefing that is information only. Test every layer as an ablation against the baseline using point-in-time timestamps. Keep LLM news scoring out of backtests entirely.

## Key Findings

### 1. Price forecasts (yearly/monthly/weekly/daily) do not earn a place in the signal

**Track record of published BTC forecasts**

| Forecast | Made by / when | Target | Outcome |
|---|---|---|---|
| Stock-to-flow "floor model" | PlanB, 2021 | $98k Nov-2021 close; $135k Dec-2021 | Closed near $57k (Nov) and $47k (Dec). PlanB then declared the floor model failed (24 Dec 2021)\[1\]\[2\] |
| S2F "$100k by Dec 2021" | PlanB, Jun 2021 pledge | $100k or model "invalidated" | BTC peaked about $69k (Nov 2021). It later fell to about $15.5k (Nov 2022)\[2\]\[3\]\[4\] |
| BTC year-end 2025 | JPMorgan (raised early Oct 2025) | $165k | BTC ended 2025 near $88k, its first full-year loss since 2022\[5\] |
| BTC year-end 2025 | Samson Mow (Feb 2025) | $1,000,000 | Same as above\[5\] |
| BTC year-end 2025 | Mike Novogratz, "once a $500,000 prophet", who per CoinDesk (30 Dec 2025) dialled back in October 2025 | $120–125k | Same as above; closest of the big names, still about 30% too high |
| End-2025 consensus | Nine tracked forecasters (deVere compilation) | Various | Median miss: 56% overshoot\[6\] |
| BTC year-end 2026 | Standard Chartered (G. Kendrick) | $300k → $150k (Dec 2025) → $100k (12 Feb 2026). In Aug 2026 said $100k "may be too low" | BTC about $78.4k on 30 Oct 2026\[7\]\[8\]\[9\]\[10\]\[11\] |
| BTC 12-month 2026 | Citigroup | $143k → $112k → $82k | Cuts tracked price down rather than leading it\[12\] |

What the record shows:
- **Revisions lag price.** Citi, Standard Chartered and Bernstein revised *downward* after ETF flows turned. Targets based on structural theses were simply left unchanged.\[6\]\[13\] A forecast that follows price is a slower, noisier version of the bot's own trend signal.
- **Directional asymmetry.** A *Finance Research Letters* study ("Can Bitcoin Investors Profit from Predictions by Crypto Experts?", vol. 46A, May 2022) found that "neutral and bearish predictions are followed by negative abnormal returns whereas bullish predictions are not associated with nonzero abnormal returns." That fits a "perma-bull" bias among pundits. If you use any external forecast at all, use it as a sentiment indicator, not a level target.
- **Model-based long-horizon forecasts** (stock-to-flow, halving-cycle models) have broken down.\[14\]\[15\] Grayscale's Zach Pandl said in July 2026 that Bitcoin "now trades on rate expectations rather than halving cycles."\[16\]
- **Prediction markets** are the best-calibrated public forecasts available. A Princeton senior thesis ("The Price is (Almost) Right") covering 188,509 resolved Polymarket markets and 1,875,570 daily prices from Nov 2022 to Dec 2025 concludes that "Polymarket prices demonstrate genuine forecasting skill overall, achieving a Brier Skill Score of 0.398." A London Business School/Yale working paper (Gomez-Cram, Guo, Kung, April 2026) finds that only 3.14% of accounts are "skilled" and that they drive most of the price discovery.\[17\] One third-party study puts crypto-price binaries at the worst calibration of the major categories (3.1 pp mean error).\[18\] The source is a vendor blog, so treat that figure as indicative only. Calibration is not edge: a market that correctly prices a 30% chance of BTC above X gives a trend follower nothing it can trade. These markets are useful for **macro event probabilities** (Fed decision odds, tariff deadlines) in the owner briefing.
- **Options-implied information is the one forecast-like input worth wiring in, but as a risk input.** Academic evidence that Deribit implied vol *forecasts realized vol better* than time-series models is mixed or negative. A horse-race study by Chi & Hao on Deribit data (Aug 2018–Mar 2020) found implied vol had an adjusted R² of about 32% for next-day realized vol, against about 64% for GARCH, and concluded that "option implied volatility adds little to the GARCH model in forecasting future realized volatilities." A 2019–2023 comparison found implied vol underperformed GARCH(1,1), and realized-variance HAR models beat daily GARCH.\[19\]\[20\] Implied vol does carry information the bot's 20–60-day realized-vol estimate lacks: **event premia and sudden repricing.** Deribit documented a visible "kink" in the BTC/ETH at-the-money term structure at the January 2024 ETF decision date, which moved closer as the date approached.\[21\] Right now the DVOL–realized gap is a live read on how much stress the market is pricing: on 30 Oct 2026, DVOL was 36 against 31 realized, ahead of a $10.86bn Deribit expiry.\[22\]

**Conclusion:** Show yearly and monthly forecasts in the briefing as context, with their track record next to them, and give them zero weight. Weekly and daily "forecasts" from AI price-prediction sites have no audited out-of-sample record and should be excluded. Use options data (DVOL level, DVOL change, term-structure kinks, put skew) as automatic risk-tightening inputs.

### 2. Catalogue of events that caused large moves (2020–2026)

| Date | Event | Type | BTC move (speed) | Notes |
|---|---|---|---|---|
| 12 Mar 2020 | COVID "Black Thursday" (WHO pandemic declaration 11 Mar) | Unscheduled macro | −37.2% in one day ($7,911→$4,971) | Largest single-day BTC loss; >$1bn longs liquidated; prior high regained 137 days after the low\[23\]\[24\] |
| 19 May 2021 | China bars banks/payments from crypto; Tesla reversal the prior week | Unscheduled regulatory | −13.8% close-to-close; about −30% intraday in about 12h ($43k→$30k) | About $8bn liquidations; −53% peak-to-trough Apr–Jul 2021\[23\]\[24\] |
| 9 May 2022 | Terra/UST de-peg | Unscheduled crypto (stablecoin) | −11.0% on the day; about −35% in a week | Contagion to Celsius (froze withdrawals 12 Jun), 3AC\[23\]\[24\]\[25\] |
| 7–8 Nov 2022 | FTX collapse (Binance dumps FTT; rescue abandoned) | Unscheduled exchange failure | −22% in under a day; about −14% on 8 Nov; −22–23% in the week | BTC hit about $15.5k; recovered pre-FTX level by mid-Jan 2023\[26\]\[27\]\[28\] |
| 10–23 Jan 2024 | US spot BTC ETF approval (decision date known) | Scheduled-ish regulatory | Flat on approval; −20.5% from the $49,021 launch-day peak to $38,975 (23 Jan) | Classic "sell the news"; GBTC outflows of $2.8bn in the first six days\[29\]\[30\] |
| 31 Jul–5 Aug 2024 | BoJ hike (sched.) + weak US payrolls (sched.) → yen carry unwind | Scheduled trigger, unscheduled cascade | BTC down as much as 16% intraday on 5 Aug; ETH >20% (worst since 2021) | BIS: BTC and ETH posted "losses of up to 20%", which points to retail margin calls; Nikkei −12.4%\[31\]\[32\]\[33\] |
| 21 Feb 2025 | Bybit $1.5bn ETH hack (Lazarus) | Unscheduled crypto (hack) | About $99k→$95k within hours; −10% over the week to $82,240 | Mixed with ETF outflows and macro; Bybit processed withdrawals within about 12h\[34\]\[35\]\[36\] |
| 2–9 Apr 2025 | "Liberation Day" tariffs (date pre-announced), then 90-day pause | Scheduled announcement + unscheduled reversal | About $88k→$82k in 48h; low about $74.5k; then +5–7% *within minutes* on the 9 Apr pause post | The rally went past $100k by early May\[37\]\[38\]\[39\]\[40\] |
| 10–11 Oct 2025 | Trump 100% China-tariff post → liquidation cascade | Unscheduled political | −14% ($122,574→$104,783); ETH −12.2%; non-BTC/ETH coins about −33% in 25 minutes | About $19bn liquidated (about 87% longs, 1.6M accounts); largest liquidation ever; CoinShares thinks the true total is higher\[41\]\[42\] |
| 5–6 Feb 2026 | Capitulation (Strategy selling, AI-equity selloff, ETF outflows) | Unscheduled flow | About −14% in a day to $60,033; then +17% the next day | Worst day since FTX; −52% from the ATH\[43\]\[44\] |
| 28 Feb–1 Mar 2026 | US/Israel strikes on Iran | Unscheduled geopolitical | About −5% in minutes toward $63k, then rebounded above $68k | Ongoing war → oil shock → US CPI above 4% by May → Fed hike repricing; BTC low about $58–59k in June\[11\]\[45\]\[46\] |
| 14 Jul 2026 | June CPI 3.5% vs 3.8% consensus | Scheduled macro | +2.9% on the day | Example of the renewed CPI sensitivity in 2026\[45\] |

Patterns that matter for the bot:
1. **Almost all of the very large one-day losses were unscheduled.** Every move of −10% or worse in a day in the table came from an unscheduled event. Scheduled events cause frequent *moderate* moves, and occasionally act as the trigger for a leverage cascade, as in August 2024.
2. **Speed beats any news reader.** Altcoins fell 33% in 25 minutes on 10 Oct 2025,\[41\] and BTC jumped 5% within minutes of the tariff-pause post.\[37\] A daily-close bot that reads news cannot react inside those windows. Only resting exchange-side stops and position size protect you.
3. **Reversals are common.** COVID, the April 2025 tariffs, February 2026 and the Iran strikes all saw sharp rebounds within days.\[43\]\[47\] A news-driven "exit on bad headline" rule would often sell the low. That is the main reason not to let headlines trigger exits automatically, except for venue and counterparty events.
4. **Leverage determines the damage.** The worst cascades (10 Oct 2025, Aug 2024, May 2021) followed crowded long positioning.\[41\]\[48\] The bot's funding z-score and open-interest inputs are better event-risk proxies than headline sentiment.

Not quantified here: Binance-specific policy or export-control events, and halving-date effects. Halvings are known years in advance and are not a short-horizon volatility event in the evidence I found.

### 3. Volatility around scheduled macro releases

| Release | Evidence | Size / timing |
|---|---|---|
| US CPI | Block Scholes (Sept 2026), six-year sample from 2020 | Surprise explains 25% of BTC's first-hour return; a 1 pp upside surprise ≈ −5.5% in 1h (0.1 pp ≈ −0.55%)\[45\] |
| US CPI (peak regime) | Block Scholes, Dec 2021–Nov 2023 window | β ≈ −13%/pp, R² 52%; a typical 1-SD surprise (0.16 pp) ≈ −1.3% within the hour\[45\] |
| US CPI (2024–25) | Block Scholes | Slope and R² "collapsed" toward zero\[45\] |
| US CPI (2026) | Block Scholes | R² back to about 22% (24-release window) and about 80% (12-release window); β −3.2%/pp (June 2026). Effect concentrated at 1h, *not* the 4–24h horizons\[45\] |
| US payrolls (NFP) | Block Scholes | 100k surprise ≈ −0.16% in 1h; sensitivity near zero in 2026\[45\] |
| FOMC | Karau (J. Int. Money & Finance, 2023) | BTC realized vol in narrow FOMC windows rose after late 2020; earlier there was no systematic response\[49\] |
| FOMC | Monetary-policy-shock study (2022) | A 1 bp hawkish 2y-yield surprise ≈ −0.25% BTC on the day; drift over the following days is several times larger and persistent\[50\] |
| FOMC 2025 | CoinGecko (via secondary source) | BTC fell after 7 of 8 FOMC meetings in 2025\[51\] |
| CPI & FOMC | KSE thesis (Nazaruk, 2026; 62 CPI, 42 FOMC events) | CPI abnormal vol builds two days before and spikes on the day; FOMC effect smaller per day but more persistent\[52\] |
| Options expiry | Deribit data (30 Oct 2026 expiry, $10.86bn) | No robust published statistic for an expiry-day effect; DVOL sat about 5 pts over realized.\[22\] Treat as briefing information, not a sizing rule |

Interpretation: the effect of a scheduled release is real but it **changes with the regime.** It matters most when the Fed is reacting to inflation, which is the case again in 2026. It is concentrated in the first hour and is mostly resolved by the next daily close. For a daily trend system, the risk is not the direction of the release. The risk is (a) *entering* just before a coin-flip gap, and (b) stop slippage if the release sets off a cascade. FOMC is the exception: the evidence for multi-day post-announcement drift is consistent with trend following, which argues against flattening positions around FOMC.\[50\]

### 4. News and sentiment signals

- **Positive academic results exist, but they are fragile.** BERT-based sentiment from news, Reddit and tweets improved daily return prediction for BTC/ETH and stayed profitable after 0.2% costs, trading every 3–18 days (Ider, 2022).\[53\]\[54\] Weekly Google-search sentiment adds predictive power in cross-sectional crypto portfolios (2026).\[55\] These are in-sample or short-sample results. None is a live, audited track record.
- **LLM backtests are contaminated by look-ahead.** Glasserman & Lin show that any backtest inside an LLM's training window is "tainted" by its knowledge of later events, and that a "distraction effect" from general knowledge also distorts the scores.\[56\]\[57\] The FINSABER framework (Li et al., KDD 2026) found that much of the reported LLM-strategy alpha disappears once survivorship, look-ahead and data-snooping biases are controlled.\[58\]\[59\] You cannot honestly backtest an LLM news classifier on 2017–2025 news with a current model.
- **Latency.** Repricing from CPI is concentrated in the first hour.\[45\] Tariff and war headlines reprice within minutes. Retail news APIs add delay on top: CryptoPanic itself says there is "no point in making requests more than once every 30 seconds" because of server-side caching.\[60\]\[61\] A daily bot is structurally last in line.
- **Manipulation and fake news are real.** The fake SEC tweet on 9 Jan 2024 moved BTC to about $47.9k before being retracted.\[62\] On 7 Apr 2025, a false "90-day pause" rumour sent BTC from $75k to over $81k and back below $79k within hours.\[63\] On the 10 Oct 2025 tariff post, House Oversight Chairman James Comer's 29 Sep 2026 letter to Hyperliquid co-founder Jeff Yan cites reports of "a $1.1 billion levered short bet spread across Bitcoin and Ether perpetual contracts, opened 30 hours before Trump's post and closed almost immediately afterward for more than $150 million in profit."

**Conclusion:** No news-sentiment signal should drive entries or exits in this bot. News is useful for (a) explaining to the owner what happened and (b) classifying *venue/counterparty* events, such as a Binance problem or a USDT de-peg, which the price-based detector should already be flagging.

## Details: Recommended Design for This Bot

### Layer 0 — Gap-aware sizing (baseline, automatic)
Exchange-side stop-market orders will slip in a cascade. On 10 Oct 2025, BTC moved −14% and alts −33% in 25 minutes.\[41\]\[42\] Add a gap-risk constraint so that the loss if the stop fills at **2–3× the stop distance** stays at or below about 1–1.5% of equity per position. At 2x leverage with vol-targeting this often won't bind for BTC, but it will for SOL. This one rule protects against unscheduled events more than any news layer could.

### Layer 1 — Scheduled-event calendar (automatic, tighten only)
- **Events:** US CPI and Employment Situation (08:30 ET, from the BLS schedule and its auto-updated ICS feed), FOMC statements (2:00 p.m. ET, press conference 2:30 p.m.; eight meetings a year, dates "tentative until confirmed at the meeting immediately preceding it"),\[64\]\[65\]\[66\]\[67\]\[68\] tariff and legal deadlines that are known in advance (entered manually), and major SEC/ETF decision dates.
- **Rules (starting parameters, to be validated):**
  - From T−24h to T+2h: **no new entries and no pyramiding adds.** If a daily-close entry signal fires inside the window, defer it to the next daily close and re-check that it is still valid.
  - **Do not force-exit existing positions** before events. Post-FOMC drift evidence and the trend logic both argue against it. Instead check that exchange-side stops are live, and cap the leverage of any position above 2x down to 2x until T+2h.
  - CPI weight is variable. If the rolling 12-release CPI→BTC R² (which you can compute yourself from first-hour returns) is above about 30%, as in 2026, apply a 0.75× vol-target multiplier to *new* positions opened in the following 48h. Otherwise apply 1.0×.
  - NFP: calendar flag only for now (sensitivity near zero in 2026).\[45\]
- **Options-expiry days:** flag in the briefing only.

### Layer 2 — Unscheduled stress detector (automatic, tighten only)
Use market-observable triggers, not headlines, because they are faster, harder to fake and can be backtested:

| Trigger | Suggested threshold (to calibrate) | Automatic action |
|---|---|---|
| BTC 1h return | \|r\| > 4σ of 1h returns, or > 5% | Freeze new entries/adds for 24h; verify stops |
| Liquidations (Binance `!forceOrder@arr`) | Rolling 1h notional > 99th percentile of own history | Same as above. If the position is on the losing side, tighten the stop to 1× ATR |
| DVOL (Deribit) | +8–10 vol points in 24h, or DVOL > 90th percentile | 0.5–0.75× vol-target for new risk for 72h |
| Funding z-score | \|z\| > 3 with OI at a 30-day high | Block adds in the crowded direction (existing regime rule) |
| Stablecoin peg | USDT or USDC < $0.99 on two venues | Freeze entries; alert the owner |
| Venue integrity | Binance API errors/withdrawal halts, USDT < $0.97, credible Binance-insolvency news | **Kill-switch:** flatten or hedge and alert the owner. This is the only news-type event allowed to force exits, because it is counterparty risk, not price risk |

Always let the trend system *keep* positions that are on the right side of a crash, such as shorts. Trend followers earn much of their return in exactly these episodes.

### Layer 3 — Owner briefing (information only, never sizes)
- **Daily:** regime state, the next 7 days of scheduled events with expected-volatility notes, DVOL and term-structure kinks, funding and OI, stress-trigger status, and an LLM summary of the top headlines with links.
- **Weekly or monthly:** bank and analyst targets *with their past miss rates*, prediction-market probabilities for macro events (Fed decision, tariffs), and ETF flow summary.
- **Suggestions to add risk** (for example, "DVOL falling, no events for 10 days, trend strong") go to the owner only. That keeps the bot's existing asymmetric "tighten automatically, suggest increases" design.

### Data sources

| Need | Source | Cost / caveats |
|---|---|---|
| DVOL history + live | Deribit `public/get_volatility_index_data` (resolutions 1s–1D; paginate via `continuation`); WebSocket `deribit_volatility_index.btc_usd` | Free, no auth; examples go back to 2020\[69\]\[70\]\[71\] |
| Liquidations | Binance `!forceOrder@arr` WebSocket | Free. Pushes only **one (since Apr 2026: the largest) liquidation per symbol per 1000ms**, so it undercounts. REST `allForceOrders` has been dead since 2021: **start logging now** or buy history (CoinGlass/Tardis)\[72\]\[73\] |
| Open interest / funding | `/futures/data/openInterestHist` (only the **latest 1 month**), `/fapi/v1/fundingRate` | Free; log OI yourself to build history\[72\]\[74\] |
| Macro calendar | BLS schedule + ICS feed; Federal Reserve FOMC calendar page; FRED API `releases/dates` (free key, no time of day) | Free. Watch for shutdown reschedules (BLS skipped the Oct 2025 jobs report)\[75\]\[76\]\[77\] |
| Prediction markets | Polymarket Gamma/CLOB read endpoints; Kalshi `external-api.kalshi.com/trade-api/v2` | Reads free, no auth (Kalshi WebSocket needs signed headers)\[78\]\[79\] |
| News | CryptoPanic API | Free tier reportedly discontinued in early 2026. Growth plan about $199/month (3,000 req/month, about 30s caching). Not needed for any automated rule\[61\]\[80\]\[81\] |

### Backtesting without look-ahead
1. **Point-in-time calendar.** Store each historical CPI, NFP and FOMC release with its *actual* UTC timestamp, including shutdown-delayed dates. Apply rules only from data available at the bot's decision time (the daily close). Note that the US 08:30 ET releases fall *after* the 00:00 UTC daily close.
2. **Ablation.** Run baseline, +Layer 0, +Layer 1, +Layer 2 separately. Report CAGR, max drawdown, Sharpe/Calmar, worst-day loss, turnover, and **missed-trend cost** (P&L lost from deferred entries). Keep a layer only if it improves drawdown or tail loss without cutting Calmar.
3. **Fixed thresholds chosen before testing.** Use round, pre-registered thresholds like those above. Do not optimise them. With about 10 major shocks in the sample, any tuning is overfitting.
4. **Stress replays.** Replay March 2020, May 2021, May and Nov 2022, Aug 2024, Oct 2025 and Feb 2026 with pessimistic stop slippage (fill at 2–3× the stop distance) to validate Layer 0.
5. **No LLM in the backtest.** Train-window contamination makes it unfixable.\[56\] If you want to evaluate an LLM classifier, paper-trade it forward only, logging its timestamped outputs for 6–12 months.
6. **Data-history limits.** DVOL history starts around 2020–21, liquidation history must be bought or logged, and OI REST history covers one month.\[74\] Layer 2 triggers that use these can only be backtested over the shorter window. Say so in the results.

## Recommendations (summary)

| Layer | Add? | Affects sizing automatically? |
|---|---|---|
| Yearly/monthly analyst & model forecasts | Briefing only | **No** |
| Weekly/daily AI price predictions | **No** (no audited record) | No |
| Prediction-market odds (macro events) | Briefing only | No |
| Options-implied data (DVOL, term structure, skew) | **Yes** | Yes, tighten only |
| Scheduled-event calendar (CPI/FOMC/NFP/tariff deadlines) | **Yes** | Yes: entry/add blackout, leverage cap, conditional CPI haircut |
| Unscheduled stress detector (price/liquidations/DVOL/funding/peg) | **Yes** | Yes, tighten only |
| Venue/counterparty kill-switch | **Yes** | Yes, may force flat |
| LLM news summary & classification | Briefing only | **No** (except as a confirming alert for the kill-switch) |

## Caveats
- Several event move sizes come from secondary sources (exchange blogs, aggregators), and figures differ by venue and by close-to-close versus intraday measurement. Examples: Aug 2024 BTC is given variously as −12% to −17%, and the Oct 2025 low as $102k on Binance spot versus $104.8k on index.\[42\]\[82\]\[83\]\[84\] Treat them as order-of-magnitude.
- The CPI-sensitivity statistics come from one research vendor (Block Scholes), and the regime-shift findings use rolling windows of only 12–24 releases. The KSE FOMC/CPI study is a student thesis.
- Polymarket accuracy figures from the platform itself (for example, "95.4% accurate 4h before resolution") are marketing claims.\[85\] Prefer the independent calibration studies.
- Some evidence is based on 2026 events still in progress (the US–Iran war, a possible Fed hike). The macro sensitivity regime may change again.
- All thresholds in the design tables are suggested starting points chosen by reasoning, not results from a backtest. Validate them with the ablation protocol before going live.
- This is information, not financial advice.

## Sources

1. [PlanB - Bitcoin Institute](https://bitcoin-institute.pages.dev/participants/planb/)
2. [Bitcoin Stock-to-Flow in 2026: A \$500K Model, a \$64K Market](https://hoge.gg/bitcoin-stock-to-flow-model-critique-2026/)
3. [What's PlanC? Bitcoin investor PlanB's stock-to-flow price model has failed](https://protos.com/bitcoin-stock-to-flow-planb-invalidated-100k-by-december-womp-womp/)
4. [Stock-to-Flow Model - Spark Glossary](https://www.spark.money/glossary/stock-to-flow)
5. [In 2025, BTC showed how spectacularly wrong price forecasts can be](https://www.coindesk.com/markets/2025/12/30/in-2025-bitcoin-showed-how-spectacularly-wrong-price-forecasts-can-be)
6. [Real-Time Bitcoin Tracking: Market Performance & Expert Crypto Commentary](https://www.devere-group.com/bitcoin-prices/)
7. [Standard Chartered Cuts Crypto Forecasts, Warns Bitcoin Could Drop to \$50K](https://www.bit.fan/en/news/list/standard-chartered-cuts-crypto-forecasts-warns-bitcoin-could-drop-to-50k-0aee63ad-keyName-c620dece)
8. [Standard Chartered Says Year-End \$100,000 Bitcoin Target May Be "Too Low" — BigGo Finance](https://finance.biggo.com/news/7b854899-80d2-4f0d-98d1-ad4803d0e660)
9. [Standard Chartered Says \$100K Bitcoin Year-End Target May Be ‘Too Low’](https://cointelegraph.com/news/standard-chartered-100k-bitcoin-2026-prediction-low)
10. [Standard Chartered Warns Bitcoin Faces \$50K Drop Before Year-End Rally](https://coinmarketcap.com/academy/article/standard-chartered-warns-bitcoin-faces-dollar50k-drop-before-year-end-rally)
11. [Standard Chartered Forecasts Bitcoin Will Retest All-Time High](https://finance.yahoo.com/markets/crypto/articles/standard-chartered-forecasts-bitcoin-retest-172300311.html)
12. [Wall Street's \$150K Bitcoin Calls for 2026 Are Subtly Falling Apart](https://news.bitcoin.com/price-predictions/wall-street-bitcoin-price-targets-2026-falling-apart/)
13. [Bitcoin Price Predictions 2026: Analysts Forecast \$38K to \$250K](https://www.coingecko.com/learn/bitcoin-price-predictions-expert-forecasts)
14. [Bitcoin Stock-to-Flow (S2F) Model Explained](https://www.coingecko.com/learn/bitcoin-stock-to-flow-model-explained)
15. [Bitcoin Rainbow Chart vs Stock-to-Flow: S2F Deviations and the Power Law Explained](https://www.lbank.com/explore/bitcoin-rainbow-chart-stock-to-flow-btc-pricing-models)
16. [Bitcoin (BTC) Price Prediction 2026, 2027-2030](https://crypto.news/price-prediction/bitcoin)
17. [Polymarket Study Finds 3.14% Drive Accuracy](https://news.bitcoin.com/polymarket-study-finds-3-14-drive-accuracy/)
18. [How Accurate Are Polymarket Predictions? A Study — Poly Syncer](https://www.polysyncer.com/blog/polymarket-prediction-accuracy)
19. [Forecasting volatility of Bitcoin - ScienceDirect](https://www.sciencedirect.com/science/article/pii/S0275531921001616)
20. [Forecasting Realised Volatility: Implied and GARCH Volatility in Bitcoin, Gold, Oil Markets](https://link.springer.com/chapter/10.1007/978-3-031-68974-1_6)
21. [Crypto 2024: The Year of Volatility - Deribit Insights](https://insights.deribit.com/industry/crypto-2024-the-year-of-volatility/)
22. [Bitcoin Options Worth \$10.86 Billion Near Expiry as Traders Seek Downside Protection](https://www.kucoin.com/news/flash/bitcoin-options-worth-10-86-billion-near-expiry-as-traders-seek-downside-protection)
23. [9 Biggest Bitcoin Crashes in History](https://finance.yahoo.com/news/9-biggest-bitcoin-crashes-history-165617173.html)
24. [Bitcoin crash history: every fall over 50% since 2011](https://cryptoticker.io/en/bitcoin-crash-history/)
25. [BITCOIN SEASON 2: Bitcoin’s Biggest Crashes Compared!](https://www.coindesk.com/podcasts/coindesk-podcast-network/bitcoin-season-2-bitcoin-s-biggest-crashes-compared)
26. [FTX Collapse Leaves Total Crypto Market Cap Under \$800B, Close to 2022 Low](https://www.coindesk.com/markets/2022/11/17/ftx-collapse-leaves-total-crypto-market-cap-under-800b-close-to-2022-low)
27. [Bitcoin drops to lowest in more than a week, ether slides as FTX collapse ripples through crypto market](https://www.cnbc.com/2022/11/21/bitcoin-btc-ether-eth-fall-as-ftx-collapse-ripples-through-market.html)
28. [Bitcoin has now recovered all its losses since FTX collapsed](https://www.cnbc.com/2023/01/17/bitcoin-has-now-recovered-all-its-losses-since-ftx-collapsed.html)
29. [Bitcoin Has Dropped About 20% Since Landmark US Spot ETFs Began Trading](https://www.itiger.com/news/1168164549)
30. [Bitcoin Has Dropped About 20% Since Landmark US Spot ETFs Began Trading](https://www.bloomberg.com/news/articles/2024-01-23/bitcoin-btc-has-fallen-about-20-since-us-spot-etfs-began-trading)
31. [BIS Bulletin No 90 The market turbulence and carry trade unwind of August 2024](https://www.bis.org/publications/bulletin-90-market-turbulence-and-carry-trade-unwind-august-2024.pdf)
32. [Yen Carry Trade Unwind 2024: Japan's Worst Day Since 1987](https://structurex-indicator.com/stories/yen-carry-unwind-2024/)
33. [bitcoin price plunges ether worst drop](https://fortune.com/2024/08/05/bitcoin-price-plunges-ether-worst-drop)
34. [StoneX Digital Asset Weekly Commentary - Bybit Hack](https://www.stonex.com/en-us/insights/stonex-digital-asset-weekly-commentary---bybit-hack-1555881/)
35. [Bybit Security Incident: Timeline of Events and FAQs](https://www.bybit.com/en/learn/this-week-in-bybit/bybit-security-incident-timeline)
36. [Hey there, Crypto Enthusiast! 🌐👋](https://solidity101.substack.com/p/crypto-weekly-february-24-2025)
37. [Trump's 90-day tariff pause sends bitcoin back to \$81,000](https://www.theblock.co/news/markets/2025-04-09-crypto-reel-china-europe-tariff-us-350257)
38. [Do US Tariffs Boost Bitcoin as an Inflation Hedge? 2025–2026 Impact](https://www.kucoin.com/blog/us-tariffs-bitcoin-inflation-hedge-2026-analysis)
39. [Trump's Liberation Day tariffs torch \$2.5 trillion in global markets, send Bitcoin tumbling](https://cryptobriefing.com/trump-tariffs-market-turmoil-bitcoin/)
40. [Bitcoin surges more than 7% in broad market relief rally as Trump pauses some tariffs](https://www.cnbc.com/2025/04/09/bitcoin-surges-more-than-7percent-in-broad-market-relief-rally-as-trump-pauses-some-tariffs.html)
41. [Inside the \$19B Flash Crash - insights4vc](https://insights4vc.substack.com/p/inside-the-19b-flash-crash)
42. [Billions in Crypto Liquidations: Inside October’s \$19B Crash](https://coinshares.com/us/insights/knowledge/billions-in-liquidations-what-happened/)
43. [Bitcoin claws back above \$70,000 after worst day since FTX crash](https://fortune.com/2026/02/06/bitcoin-claws-back-above-70000-after-worst-day-since-ftx-crash/)
44. [Bitcoin drops to \$60,000: worst crash since FTX collapse - Crypto Valley Journal](https://cryptovalleyjournal.com/hot-topics/news/bitcoin-drops-to-60000-worst-crash-since-ftx-collapse/)
45. [Is Bitcoin showing greater sensitivity to US CPI releases again? | Block Scholes Research](https://www.blockscholes.com/institutional-research/is-bitcoin-showing-greater-sensitivity-to-us-cpi-releases-again)
46. [Bitcoin Suddenly Braced For A Massive Price Shock](https://www.forbes.com/sites/digital-assets/2026/03/01/bitcoin-suddenly-plunges-as-markets-brace-for-iran-war-price-crash/)
47. [Bitcoin, crypto prices rebound as Trump pauses tariffs for 90-days](https://fortune.com/crypto/2025/04/11/bitcoin-crypto-markets-whipsawed-tariff-turmoil/)
48. [Crypto’s Black Friday: Inside The \$19 Billion Market Meltdown](https://www.forbes.com/sites/digital-assets/2025/10/13/cryptos-black-friday-inside-the-19-billion-market-meltdown/)
49. [Monetary policy and Bitcoin - ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0261560623000815)
50. [Monetary policy shocks and Bitcoin prices - ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S027553192200099X)
51. [How FOMC and CPI Move Bitcoin: A 2026 Macro Calendar](https://financefeeds.com/how-macro-events-move-crypto/)
52. [BITCOIN’S REACTION TO U.S. FOMC AND CPI ANNOUNCEMENTS by Illia Nazaruk](https://kse.ua/wp-content/uploads/2026/05/illia-nazaruk_268722_assignsubmission_file_nazaruk_final_thesis.pdf)
53. [Forecasting Cryptocurrency Returns from Sentiment Signals:](https://arxiv.org/pdf/2204.05781)
54. [Cryptocurrency Return Prediction Using Investor Sentiment Extracted by BERT-Based Classifiers from News Articles, Reddit Posts and Tweets](https://www.semanticscholar.org/paper/Cryptocurrency-Return-Prediction-Using-Investor-by-Ider/37a123f00f93858c763da4dac3b99e528ac81160)
55. [Predictive sorting of cryptocurrencies based on fundamentals and sentiment - ScienceDirect](https://www.sciencedirect.com/science/article/pii/S1042443126000016)
56. [Assessing Look-Ahead Bias in Stock Return Predictions Generated By GPT Sentiment Analysis](https://arxiv.org/html/2309.17322)
57. [Assessing Look-Ahead Bias in Stock Return Predictions Generated By](https://arxiv.org/pdf/2309.17322)
58. [MemGuard-Alpha: Limits of Membership Inference for Detecting and Filtering Memorization-Contaminated Signals in LLM-Based Financial Forecasting](https://arxiv.org/pdf/2603.26797)
59. [Can LLM-based Financial Investing Strategies Outperform the Market in Long Run?](https://arxiv.org/html/2505.07078v4)
60. [How-To-Integrate-The-Cryptopanic-Api](https://cph-front-lb.cryptopanic.com/guides/how-to-integrate-the-cryptopanic-api)
61. [Welcome to the CryptoPanic API - FX Panic](https://fxpanic.com/developers/api/about)
62. [SEC Approves Spot Bitcoin ETFs: What That Means for Investors](https://www.kiplinger.com/investing/cryptocurrency/spot-bitcoin-etf-sec-approval)
63. [90-Day tariff pause rumor triggers short-lived market rally](https://www.bitget.com/news/detail/12560604690787)
64. [CPI & NFP Release Dates 2026 & 2027 (BLS)](https://chartwatch.tv/cpi-nfp-release-dates)
65. [Department of Labor Logo United States Department of Labor](https://www.bls.gov/schedule/2020/04_sched_list.htm)
66. [The Fed - Meeting calendars and information](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm)
67. [Meeting calendars, statements, and minutes (2021-2027)](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm?m=1%27%22)
68. [January 2026](https://www.federalreserve.gov/newsevents/2026-january.htm)
69. [deribit\_volatility\_index.(index\_name) - Deribit API Documentation](https://docs.deribit.com/subscriptions/upcoming/market-data/deribit_volatility_indexindex_name)
70. [Deribit Options Data Collection - Best Practices Guide](https://docs.deribit.com/articles/options-data-collection-best-practices)
71. <https://docs.deribit.com/api-reference/market-data/public-get_volatility_index_data.md>
72. [Change Log](https://developers.binance.com/docs/derivatives/change-log)
73. [Liquidation Order Streams](https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Liquidation-Order-Streams)
74. [Open Interest Statistics](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Open-Interest-Statistics)
75. [Department of Labor Logo United States Department of Labor](https://blsmon1.bls.gov/bls/2025-lapse-revised-release-dates.htm)
76. [\# US Economic Calendar – BLS, BEA & Fed Official Release Dates (\`polychrome\_haymow/us-econ-calendar\`) Actor](https://apify.com/polychrome_haymow/us-econ-calendar.md)
77. [Economic calendar API for US high-impact releases, live and historical · Issue #25 · afloret-services/AITradingBot · GitHub](https://github.com/afloret-services/AITradingBot/issues/25)
78. [Kalshi API Guide 2026: Auth, WebSocket, Candlesticks & SDKs](https://blog.predictefy.com/kalshi-api-guide)
79. [Polymarket API Documentation Guide](https://nextpredict.io/learn/polymarket-api-docs/)
80. [CryptoPanic: News, API, Filters & Setup](https://www.anygen.io/showcase/cryptopanic/index.html)
81. [CryptoPanic Python API Docs](https://dlthub.com/context/source/cryptopanic)
82. [BeInCrypto on X: "ONE YEAR AGO: THE OCTOBER 10 CRYPTO CRASH On October 10, 2025, a crypto sell-off following President Donald Trump’s announcement of additional 100% tariffs on China escalated into a liquidation cascade. More than \$19 billion in leveraged positions were liquidated within 24 hours.… / X](https://x.com/beincrypto/status/2108678023293604213)
83. [Yen Carry Trade Unwind: August 2024 Flash Crash Explained](https://khancapitals.com/yen-carry-trade-flash-crash-august-2024/)
84. [Yen Carry Trade Unwind: 2024 Episode and What Comes Next - Lambda Finance](https://www.lambdafin.com/articles/yen-carry-trade-unwind)
85. [Polymarket Debuts 5-Minute Bitcoin Prediction Markets With Instant Settlement](https://coinmarketcap.com/academy/article/polymarket-debuts-5-minute-bitcoin-prediction-markets-with-instant-settlement)
