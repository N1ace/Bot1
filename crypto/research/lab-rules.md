# Day-trade lab rules

Read this before writing a tactic. The rules are enforced by `src/daytrader/lab.py`.

## How a tactic works

One file in `src/daytrader/tactics/NAME.py`, using only numpy, pandas and `cryptobot.indicators`:

```python
HYPOTHESIS = "One or two sentences: which behaviour gets exploited, and why it should last."
SETTINGS = {"lookback": 24, "k_stop": 1.5}     # fixed before testing
TUNABLE = ("lookback", "k_stop")               # numeric settings the lab moves +-20%. At most 4.

def signals(bars, s):
    """Return a DataFrame with bars' index and columns side, stop, target, max_bars."""
```

`bars` holds 1-hour candles: open, high, low, close, volume, `funding` (last settled rate) and
`btc_close`. The row for a bar may use only that bar and earlier ones.

- `side`: +1 long, -1 short, 0 nothing. Decided at the bar's close, filled at the next open.
- `stop`: distance from entry, in price units. Required.
- `target`: distance from entry, or NaN for no target.
- `max_bars`: hours to hold at most.

Every position is closed by the end of the UTC day. Costs are 0.05% per side plus slippage,
and all the pessimistic fill rules are in `src/daytrader/sim.py`.

## The rules

1. One test per tactic. Changing a tested file is refused: a change needs a new name.
2. The lab allows 12 tactics in total. Only the owner can raise that.
3. Everything from 2025-05-01 is hidden. `final` opens it once, only after passing `test`.
4. Never look at results and adjust. Write the next idea from the research, not from the last score.
5. Don't read `data/` or the hidden period by any other route, and don't edit `lab.py` or `sim.py`.

## Passing `test`

300+ trades, net Sharpe above 0.8, above 0.4 with double costs, stable at +-20%, profitable in
60% of years, and a skill-or-luck score above 0.95 counting every attempt.

## What the research says

Full text: [forecast-news-research.md](forecast-news-research.md).

- News and forecasts have no place in a signal. News can't be honestly backtested.
- Scheduled releases move BTC most in the first hour, and the strength changes with the regime.
- The biggest losses come from unscheduled shocks, so a stop is required on every trade.
- Crowded leverage, seen through funding and open interest, marks the worst cascades.
- Sharp rebounds are common, so exits on panic often sell the low.
- Costs: a taker round trip is about 0.16% with slippage. A typical BTC hour moves about 0.6%, so
  costs take about a quarter of a typical hourly move. Faster than 1-hour bars isn't testable here.

Already rejected on 4-hour bars: Bollinger and RSI mean reversion in chop (hit the kill switch),
and EMA pullbacks in a trend (no edge on unseen data). Don't retry those under another name.
