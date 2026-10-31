# FTMO day-trading bot: plan and decisions

Decided 2026-10-31. Crypto is set aside in `crypto/`. Numbers below come mostly from third-party sites unless
marked official, and FTMO changes its rules, so confirm them before paying.

## Decisions

| Topic | Decision |
|---|---|
| Firm | FTMO |
| Product | FTMO Classic (CFD), 2-Step, smallest account |
| Backup product | FTMO Futures, Pro 50K, if bots are confirmed there |
| Markets | US100 first, US500 second, XAUUSD third. EURUSD later |
| Order | Build and backtest with all fees first. Pay only after a tactic passes |
| Style | Day trading, no scalping |

## Why these markets

Cost of one round trip. Spread figures are an undated snapshot and need checking in the platform.

| Symbol | Commission | Spread snapshot | Estimated cost | Note |
|---|---|---|---|---|
| US100 | None (FTMO blog) | ~1.7 points | ~0.01% | Big daily moves for its cost |
| US500 | None (FTMO blog) | ~0.55 points | ~0.01% | Steadier, the same news drives it |
| XAUUSD | ~0.0014% of volume, sources differ | Not found | Check | Strong moves, spread widens on news |
| EURUSD | ~$5 per lot round trip | Not found | ~0.01% | Small daily moves, heaviest professional competition |
| Crypto CFDs | Not checked | Not checked | Likely highest | Weekends unlike the rest; we've left crypto |

Our crypto model cost about 0.16%, so these are roughly 15 times cheaper, if the spreads hold.

## FTMO Classic 2-Step rules to build in

- Step 1 target 10%, step 2 target 5%. No time limit reported. At least 4 trading days per step.
- 5% daily loss limit, measured on equity and including open trades. Resets at midnight Central European Time.
- 10% total loss limit, static.
- Leverage reported: indices 1:50, forex 1:100, metals 1:30.
- No trading within 2 minutes of high-impact news on funded accounts (one source).
- Banned: tick scalping that exploits delays, latency or high-frequency arbitrage, martingale, grid,
  hyperactive EAs, mirroring others' trades.

The bot stops well before each limit. Starting values: stop for the day at -3%, and stop everything at -7%.

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

- [ ] Does FTMO accept residents of Israel?
- [ ] FTMO's official bot rules, on Classic and on Futures
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
