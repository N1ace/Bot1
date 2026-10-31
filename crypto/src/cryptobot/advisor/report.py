"""Daily report and weekly review texts (bot-roadmap-and-principles.md Part 4).

Plain text (no Markdown) so Telegram shows it exactly as written.
"""

from __future__ import annotations

import math
from datetime import date

from cryptobot.advisor.tips import Tip
from cryptobot.levels import LEVEL0_GATE_DAYS, level_name
from cryptobot.observer.regime import MarketRegime, SymbolRegime


def _pct(x: float, digits: int = 1, sign: bool = False) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    return f"{x * 100:+.{digits}f}%" if sign else f"{x * 100:.{digits}f}%"


def daily_report(day: date, level: int, regimes: dict[str, SymbolRegime], market: MarketRegime,
                 problems: list[str], streak: int, tip: Tip, pending: list[dict]) -> str:
    lines = [f"Daily report {day:%Y-%m-%d} (UTC close) - level {level_name(level)}", ""]

    lines.append("1) MARKET")
    lines.append(f"BTC vs 200-day average: {market.btc_close:,.0f} vs {market.btc_sma200:,.0f} "
                 f"({'rising' if market.sma200_rising else 'falling'}) -> {market.state}")
    for r in regimes.values():
        crowd = "  CROWDED" if r.crowded else ""
        lines.append(f"{r.symbol}: {r.close:,.2f} ({_pct(r.change_1d, sign=True)} day, {_pct(r.change_30d, sign=True)} "
                     f"30d) | {r.trend_label} (ER {r.er20:.2f}, ADX {r.adx14:.0f}) | volatility {r.vol_label} "
                     f"({_pct(r.vol30_ann, 0)}/yr) | funding {_pct(r.funding_7d_ann, 0)}/yr (7d avg){crowd}")
    if not math.isnan(market.corr_btc_eth_60d):
        lines.append(f"BTC-ETH correlation (60d): {market.corr_btc_eth_60d:.2f}")
    lines.append("")

    lines.append("2) POSITIONS")
    lines.append("None - level 0 never trades.")
    lines.append("")
    lines.append("3) RISK USAGE")
    lines.append("Nothing at risk (no positions).")
    lines.append("")

    lines.append("4) WHAT THE BOT DID")
    status = "all data complete" if not problems else f"{len(problems)} data problem(s): " + "; ".join(problems[:3])
    lines.append(f"Updated candles, funding and positioning data - {status}.")
    lines.append(f"Level 0 exit gate: {min(streak, LEVEL0_GATE_DAYS)} of {LEVEL0_GATE_DAYS} clean days in a row.")
    for r in regimes.values():
        lines.append(f"Strategy (info only): {r.symbol} would be {round(r.signal * 7)}/7 long.")
    lines.append("")

    lines.append("5) TIP OF THE DAY")
    lines.append(tip.text)

    if pending:
        lines.append("")
        lines.append("WAITING FOR YOUR ANSWER")
        for s in pending:
            lines.append(f"#{s['id']} {s['title']} -> reply /approve {s['id']} or /reject {s['id']}")
    return "\n".join(lines)


def weekly_review(week_label: str, level: int, runs: list[tuple[str, str]], streak: int,
                  regimes: dict[str, SymbolRegime], market: MarketRegime, new_suggestions: list[dict]) -> str:
    """Markdown, saved under reports/ and sent (shortened) to Telegram."""
    clean = sum(1 for _, status in runs if status == "ok")
    lines = [f"# Weekly review {week_label}", "",
             f"**Level:** {level_name(level)}  ", f"**Level 0 gate:** {min(streak, LEVEL0_GATE_DAYS)} of "
             f"{LEVEL0_GATE_DAYS} clean days in a row  ",
             f"**Runs this week:** {len(runs)} ({clean} clean)", "",
             "## Market at week end", "",
             f"- BTC {market.btc_close:,.0f} vs 200-day average {market.btc_sma200:,.0f} -> **{market.state}**"]
    for r in regimes.values():
        lines.append(f"- {r.symbol}: {r.trend_label}, volatility {r.vol_label}, funding {_pct(r.funding_7d_ann, 0)}/yr, "
                     f"30-day change {_pct(r.change_30d, sign=True)}, strategy would be {round(r.signal * 7)}/7 long")
    lines += ["", "## Performance vs backtest", "",
              "No trades at level 0 - nothing to compare yet.", "", "## Suggestions", ""]
    if new_suggestions:
        for s in new_suggestions:
            lines.append(f"- **#{s['id']} {s['title']}**  \n  Evidence: {s['evidence']}  \n"
                         f"  If approved, you change: `{s['config_change']}`  \n"
                         f"  Answer in Telegram: `/approve {s['id']}` or `/reject {s['id']}`")
    else:
        lines.append("None this week.")
    return "\n".join(lines)


def approval_reply(s: dict) -> str:
    if s["status"] == "approved":
        return (f"Recorded: #{s['id']} approved. The bot does NOT change settings itself. To apply it, edit "
                f"config and set: {s['config_change']}")
    return f"Recorded: #{s['id']} rejected. Nothing changes."
