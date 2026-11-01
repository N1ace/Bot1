# FTMO day-trading bot: plan and decisions

Decided 2026-10-31. Crypto is set aside in `crypto/`. Numbers below come mostly from third-party sites unless
marked official, and FTMO changes its rules, so confirm them before paying.

## Decisions

| Topic | Decision |
|---|---|
| Firm | FTMO |
| Product | FTMO Classic (CFD), 2-Step, Standard, $10,000 first, then $100,000 |
| Platform | MetaTrader 5, on the owner's Windows PC. Check the Python package installs first |
| Backup product | FTMO Futures, Pro 50K, if bots are confirmed there |
| Markets | US100 first, US500 second, XAUUSD third. EURUSD later |
| Order | Build and backtest with all fees first. Pay only after a tactic passes |
| Style | Day trading, no scalping |

## What the index research report changed (owner's report, 2026-10-31)

Most of it comes from third-party or affiliate sites. FTMO's forbidden-practices page is the one official FTMO source.

- **The rules decide the outcome more than the entry signal.** The simulator must replay FTMO's rules and report how
  often an attempt passes, not only profit. A reported 14% of challenges pass and 7% of buyers ever get paid.
- **Avoid rushing.** In the report's simple model, with Sharpe 1.0 and needing +10% before -10%: about 88% pass at 10%
  yearly volatility, taking about 9 months; about 62% pass at 40%. Real odds are lower. No time limit is why we keep
  2-Step.
- **Tactic order after the owner's own:**
  1. Noise-area momentum with a VWAP trailing stop: checked twice an hour, flat at the US close. Published Sharpe 1.33
     after costs on SPY, 2007-2024, no live record. The exit and the sizing made most of the gain.
  2. First half-hour predicts last half-hour: one trade a day. Data ends 2012, so it may have faded.
- **Skip without testing:** 5-minute opening-range breakout (about zero after CFD costs, 24% win rate), ICT/fair-value
  gaps, gap fills, overnight drift, pre-FOMC drift, grid and martingale. Our crypto trend style doesn't fit either.
- **Filters, not signals:** 200-day average and VIX or volatility level can adjust risk.
- **Every test also beats a random-direction version**, a new gate for the lab.
- **Volume gap:** CFD feeds have no true exchange volume, so VWAP needs a stand-in. Decide and test this before the tactic.
- **US100 and US500 are one bet.** Never count them as diversification.
- **All session times use `America/New_York`.** Israel and US clocks drift apart for a few weeks each year.
- **Risk per trade is capped by the 20-loss test:** 0.5% of the account, so 20 straight losses cost about the whole 10%
  limit. That is the ceiling. The bot's -7% stop means testing below it.
- **Keep the crypto plumbing, not the tactic:** stop-based sizing, broker-side stops, reconciliation, kill switch, journal.
- **Tax:** a payout may count as business income, not capital gains. See an Israeli accountant before the first payout.

## Pricing differs by product

Classic and Futures can't be compared on the headline price.

- **Futures:** a monthly charge (from the checkout screens below), so a slow attempt costs more the longer it runs.
- **Classic 2-Step:** a one-time payment (checkout), reported as refunded with the first reward, with no time limit.

Classic 2-Step checkout prices, 2026-11-01, including VAT, USD account, paid in EUR:

| Balance | Standard | Fee as % of balance | Swing |
|---|---|---|---|
| $10,000 | EUR 89 | 0.89% | EUR 99 |
| $25,000 | EUR 250 | 1.00% | EUR 279 |
| $50,000 | EUR 345 | 0.69% | EUR 379 |
| $100,000 | EUR 439 (19% off, normally EUR 540) | 0.44% | EUR 599 |
| $200,000 | EUR 1,080 | 0.54% | EUR 1,080 |

First attempt: Standard $10,000 at EUR 89, a test of the real bot. If it passes and works, move to Standard $100,000,
the cheapest for its size. Check first that the smallest lot allows our risk per trade on $10,000.

Compare the expected total cost per attempt: price, months needed and the chance of passing. The simulator supplies the
last two. Until then Classic stays the pick, because our plan is to pass slowly.

## Classic products (FTMO checkout, 2026-11-01)

| | 2-Step (our pick) | 1-Step | Instant |
|---|---|---|---|
| Profit target | 10%, then 5% | 10% | None |
| Total loss | 10% | 10%, trails at end of day | 10%, trails during the day |
| Daily loss | 5% | 3% | 3% |
| Consistency | None shown | 50% | 30% |
| Minimum days | 4 | None shown | None shown |
| Trading period | Unlimited | Unlimited | Unlimited |
| Rewards | Up to 90% | 90% | 90% |

Instant is the worst fit: its limit trails open profits, and a 30% cap penalises our best days. Its price isn't known yet.

## FTMO symbol details (MT5 Specification, trial account, 2026-11-01)

| | US500.cash | UK100.cash (not a target) |
|---|---|---|
| Contract size, profit currency | 1, USD | 1, GBP |
| Min lot, step | 0.01, 0.01 | 0.01, 0.01 |
| Commission | 0 | 0 |
| Margin | 2% (1:50) | 2% (1:50) |
| Stops level | 0 | 0 |
| Swap long, short (points) | -175.78, -8.27 | -261.64, +6.71 |
| Triple swap | Friday | Friday |
| Trading hours (server time) | Mon-Fri 01:05-23:50 | Mon-Thu 01:05-23:50, Fri 01:05-22:50 |

Orders need Fill or Kill or Immediate or Cancel. Tick size and value are rounded on screen: read exact values from MT5.
Server clock: the US open showed at 16:30 server time on 2026-10-26 to 30, while US summer time still applied. That
means New York + 7 hours, so the US close is 23:00 server. Confirm once after the US clock change.

Measured through the `MetaTrader5` package (5.0.6231, installed in `.venv`, read-only so far), 2026-07-22 to 2026-10-30,
US hours, from the spread stored on 1-minute bars: these may hide brief news spikes and exclude slippage, and come from
a demo account. Bars available: 1-minute about 100 days, hourly from December 2017.
US100.cash: contract 1 in USD, min lot and step 0.01, commission 0, margin 2% (about $568 per lot), stops level 0,
swap -719.83 long and +22.71 short (points), triple swap Friday, hours Mon-Fri 01:05-23:50.

XAUUSD: contract 100 oz, min lot and step 0.01, commission 0.0007% per side, margin about $7,748 per lot, stops level 0,
swap -64.8 long and -4.2 short (points), triple swap Wednesday, hours 01:05-23:50 every weekday including Friday.

## Why these markets

Cost of one round trip. Spread figures are an undated snapshot and need checking in the platform.

| Symbol | Commission | Spread snapshot | Estimated cost | Note |
|---|---|---|---|---|
| US100 | None (MT5) | 1.45 points (measured) | ~0.005% | Big daily moves for its cost |
| US500 | None (MT5) | 0.60 points (measured) | ~0.008% | Steadier, the same news drives it |
| XAUUSD | 0.0014% round trip (MT5) | $0.40 (measured) | ~0.012% | Strong moves, spread widens on news |
| EURUSD | ~$5 per lot round trip | Not found | ~0.01% | Small daily moves, heaviest professional competition |
| Crypto CFDs | Not checked | Not checked | Likely highest | Weekends unlike the rest; we've left crypto |

Our crypto model cost about 0.16%, so these are roughly 15 times cheaper, if the spreads hold.

## FTMO Classic 2-Step rules to build in

- Step 1 target 10%, step 2 target 5%. Unlimited trading period. Minimum 4 trading days (checkout; per step reported).
- 5% daily loss limit, including open trades. Resets at midnight Central European Time. Sources differ on whether
  it starts from balance or equity at midnight: the bot uses the stricter, whichever is lower.
- 10% total loss limit, static.
- Leverage reported: indices 1:50, forex 1:100, metals 1:30.
- Funded Standard accounts: no opening or closing within 2 minutes of major news, and close before weekends and
  before rollover gaps over 2 hours. Challenges reportedly have neither rule. The bot follows them from day one, flat
  5 minutes either side of CPI, NFP and FOMC, so it doesn't learn habits that break funded rules.
- Official page (modified 2026-10-12): hyperactive means over 2,000 server requests a day. Also banned: gap trading
  (opening within 2 hours of a close of 2 hours or more), sharply varying position sizes, and "AI, software,
  ultra-high-speed tools or mass data entry" giving an unfair edge. That wording is broad: get FTMO's written answer.
- Size changes stay small from day to day. No tick-by-tick stop updates.
- Banned: tick scalping that exploits delays, latency or high-frequency arbitrage, martingale, grid,
  hyperactive EAs, mirroring others' trades.

The bot stops well before each limit. Starting values: stop for the day at -3%, and stop everything at -7%.

## Lab results

Data: FTMO's own 1-minute bars. Test period 2021-10 to 2025-04, on US100. Hidden from 2025-05, used only by a final run.
Full results are in `ftmo/lab_log.json`. 8 of 12 tactics used. The spread floor is in price points, which overcharges years when prices were lower, gold most of
all. New tactics should set it as a percentage. That applies to new tests only.

| Tactic | Result | Why |
|---|---|---|
| noise_area | FAIL, 2026-11-01 | 6 of 7 gates passed, Sharpe 1.48. Failed stability: a 20% settings change moved Sharpe by 0.52, limit 0.25 |
| first_half_hour | FAIL, 2026-11-01 | Sharpe -1.39, lost in all 5 years, no better than random direction |
| vwap_fade | FAIL, 2026-11-01 | Sharpe -0.97, lost in all 5 years, negative even with no costs |
| quiet_day_breakout | FAIL, 2026-11-01 | Sharpe 1.09, but stability gap 0.88 and luck check 0.84 |
| big_day_last_half_hour | FAIL, 2026-11-01 | Sharpe -0.17, costs took a small gross gain |
| layered_breakout | FAIL, 2026-11-01 | Owner-approved exception. Sharpe 1.19, stability gap 0.38, luck check 0.84 |
| noise_area_gold | FAIL, 2026-11-01 | noise_area unchanged on XAUUSD. Sharpe -0.67, lost in all 5 years, beat random 99% |
| dax_overnight_range | FAIL, 2026-11-01 | Sharpe 0.76, luck check 0.46, beat only 87% of random. Stable, and all 4 years positive. GER40 commission assumed zero, not read from MT5 |

Lessons: the two good results both enter on a break during the day and hold to the close. Betting against moves lost, and
30-minute trades left too little after costs. Stability is the shared weakness. Any new gate wording applies only to new
tactics and can't rescue a failed one.

Failures stand. No lowering a gate, and no small variations under a new name.

## What the tactics report changed (owner's report, 2026-11-01)

Secondary and vendor sources are mixed in. Treat every published backtest as an upper bound.

- **Published edges decay.** noise-band momentum is reported at Sharpe 0.39 for May 2024 to March 2026, against 1.34
  before it, and 9.4% against 29.5% for buy-and-hold. One point of stop slippage removed 22% of its edge on NQ micros.
  Expect far less than our 1.48 in the demo. Our hidden data starts in May 2025, inside that weak period.
- **Our failures fit this report.** Unconditional last-30-minute momentum is reported gone since 2022 (t = 0.6), which
  matches first_half_hour and big_day_last_half_hour. VWAP reversion is reported to need positive gamma, and ours had no gate.
- **Regime may be the missing piece.** Dealer gamma sign is the best-supported driver: negative favours momentum,
  positive favours reversal. Needs paid data. VIX above 17-20 is a cheaper stand-in. Adding either to a failed tactic is
  a new tactic and needs the owner's approval.
- **Next candidate: DAX overnight-range breakout.** Range 03:00-09:00 Frankfurt, trade the break at the Xetra open, stop
  at the other side, 2R target, flat by 13:00 CET. Vendor backtest, seller of VPS: PF 1.25, Sharpe 1.46, 2013-2026. One
  trade a day at 10:00 Israel time. Needs GER40 history and a simulator that handles other sessions.
- **Swing option:** IBS or RSI(2) pullbacks above the 200-day average. Needs the FTMO Swing account (1:30 leverage) and
  overnight costs in the simulator. Edge has roughly halved since the 1990s.
- **Skip:** turn-of-month, the 80% rule (measured 40-67%), closing-imbalance trades, news straddles, and all true
  arbitrage. Feed arbitrage is banned at FTMO, and a Python bot is too slow to compete.
- **Bot additions before any paid account:** CPI, NFP and PPI blackouts, cancel orders 6 minutes before news because a
  stop filling inside FTMO's window can count, a spread filter at 2x normal, and a volatility-spike block.

## Demo forward test

`ftmo/bot.py` runs noise_area on an MT5 demo, to collect results on new prices. noise_area failed its lab test, so this
is not approval for a paid account. Run for at least 3-6 months, then compare with the backtest.

- Dry run by default. Orders need `--place-demo-orders --login <number>`, and a non-demo account is refused.
- Same code as the backtest: bot and backtest levels matched at 24 sample moments.
- Stops at the broker, -3% day and -7% total limits, flat by 15:59 New York and around FOMC, 1,000 requests a day at most.
- A paid challenge also shows as a demo account, so the login check is what keeps the bot off one.
- Two safety reviews done, the second with no blockers. Not done: a longer demo than the 14-day trial, and a CPI and NFP flat period.

## Build order

1. Owner asks FTMO: Israel accepted? Own bot allowed? What counts as hyperactive? Do Classic and Futures differ?
2. Open a free FTMO trial or demo, if offered, to read exact spreads, swaps and contract sizes.
3. Get price history for US100, US500, XAUUSD.
4. Simulator with real costs and challenge rules, counting how often an attempt passes or fails.
5. Lab with the same safeguards as crypto: limited tactics, one test each, hidden data, look-ahead check.
6. Test the owner's tactic first, then researched ones.
7. Bot on a demo account. Classic means MetaTrader 5, which has a Python package for Windows (to verify).
8. Pay for the smallest challenge only after steps 6 and 7 work.

## Open questions

- [x] ~~Does FTMO accept residents of Israel?~~ Yes for buying: the owner registered with Israeli residency and can buy (2026-11-01)
- [x] ~~Are bots allowed?~~ Yes within the request limit, per the owner (2026-11-01)
- [ ] Does that answer cover Futures as well as Classic?
- [x] ~~Classic checkout price for each account size~~ See the pricing table
- [ ] Smallest lot on US100 and US500, and its value per point
- [x] ~~Israel at payout, and which payout method works there~~ Israeli bank transfer works, per the owner (2026-11-01)
- [x] ~~Can a VPS run MT5 and our software?~~ Yes, per the owner (2026-11-01)
- [x] ~~Payout minimum and cap~~ FTMO FAQ: bank wire minimum $20 closed profit, no FTMO commission, no bank wire cap shown. Intermediary banks may charge. Instant card methods up to $20,000
- [ ] Does "AI, software" in the forbidden list cover our own rule-based bot? Written answer
- [ ] Exact fee for the smallest Classic 2-Step account
- [ ] Exact spreads and overnight costs per symbol
- [ ] Are swap costs avoided if every position closes the same day?

## FTMO Futures, for reference (from the owner's checkout screens, 2026-10-31)

| | Growth 50K | Pro 50K | Growth 150K | Pro 150K |
|---|---|---|---|---|
| Price/month | $119 | $139 | $229 | $269 |
| Target | $3,000 | $3,000 | $9,000 | ~$9,000 (partly hidden) |
| Trailing drawdown | $2,000 | $3,000 | $5,000 | $6,000 |
| Daily loss, evaluation | None | $1,000 hard | None | $2,000 hard |
| Daily loss, funded | $1,000 soft | $1,000 hard | $3,000 soft | $2,000 hard |
| Consistency | 40% | 50% | 40% | 50% |
| Max contracts | 5 mini | 5 mini | 15 mini | 15 mini |
| Withdrawable | 50% | 100% | 50% | 100% |
| Payout cap | $2,500 | $5,000 | $4,000 | $8,000 |

The drawdown is trailing and equity must stay above it at all times. FTMO's own commission is reported at
$0.50 per side on minis and $0.20 on micros, plus exchange fees. FTMO's FAQ reports 30 instruments.

## News engine

`news_engine/` follows `news-analog-engine-spec.md`, independent of the bot. First stage: free data, move size only, US100,
US500 and GER40. FOMC is scored. CPI and NFP need a free FRED key in the root `.env`, as `FRED_API_KEY=`. Paid consensus
data waits until this stage proves useful. Reports use size, never direction, so the lab's hidden period stays unused.
