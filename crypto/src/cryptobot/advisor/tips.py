"""Tip of the day: one short, data-based observation (bot-roadmap-and-principles.md Part 4).

Rules enforced here:
- every tip uses the bot's own numbers
- the first time a concept appears it is explained in one or two sentences
- never suggests more risk; never uses news, social media or price predictions
- at most one tip per day; the same concept is not repeated within 3 days
  unless it is a data problem or a crowding/funding warning
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from cryptobot.observer.regime import MarketRegime, SymbolRegime

FUNDING_HALVE_ANN = 0.30   # strategy-risk-spec layer 5
FUNDING_EXIT_ANN = 0.60

CONCEPTS = {
    "data_health": "The bot can only be trusted if its data is complete. Level 0 passes after 14 clean days in a row.",
    "funding": "Funding is a fee paid between longs and shorts every few hours to keep the futures price near the "
               "real price. When it is positive, longs pay. It adds up: 0.01% every 8h is about 11% a year.",
    "crowding": "'Crowded' means many traders are betting the same way with borrowed money. Funding is unusually "
                "high and open interest is rising fast - sharp drops that force them out are more likely.",
    "volatility": "Volatility is how much the price swings. The bot compares this month to the last year; when it "
                  "is very high, the rules cut position sizes.",
    "chop": "The efficiency ratio (ER) measures how straight the price moved: 1 = straight line, near 0 = "
            "zig-zag. Trend strategies lose small amounts again and again in zig-zag (chop) markets.",
    "trend_state": "The 200-day average is the average price of the last 200 days. Price above a rising average = "
                   "the big trend is up; below a falling one = down.",
    "correlation": "Correlation shows how much two coins move together (1 = exactly together). BTC and ETH usually "
                   "move together, so two positions are closer to one big bet - that's why risk is capped for both "
                   "together.",
    "strategy_signal": "The strategy checks 7 time windows (10 to 250 days). Each window says 'long' when the price "
                       "broke above its highest close of that window. The more windows say 'long', the more of "
                       "the planned size is used.",
}


@dataclass(frozen=True)
class Tip:
    concept: str
    text: str


def _pct(x: float, digits: int = 1) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:.{digits}f}%"


def candidates(regimes: dict[str, SymbolRegime], market: MarketRegime, problems: list[str]) -> list[Tip]:
    """All tips that apply today, most important first."""
    out: list[Tip] = []
    if problems:
        out.append(Tip("data_health", f"Data check found {len(problems)} problem(s), e.g. '{problems[0]}'. "
                                      "Today does not count toward the Level 0 clean-day streak."))
    for r in regimes.values():
        if r.crowded:
            out.append(Tip("crowding", f"{r.symbol} looks crowded: funding is {r.funding_z90:.1f} standard deviations "
                                       f"above its 90-day normal and open interest rose {_pct(r.oi_change_7d)} in 7 days. "
                                       "The rules would block new longs here."))
    for r in sorted(regimes.values(), key=lambda r: -abs(r.funding_7d_ann)):
        if abs(r.funding_7d_ann) > FUNDING_HALVE_ANN:
            side = "longs pay" if r.funding_7d_ann > 0 else "shorts pay"
            action = "close" if abs(r.funding_7d_ann) > FUNDING_EXIT_ANN else "halve"
            out.append(Tip("funding", f"Funding on {r.symbol} averaged {_pct(r.funding_7d_ann, 0)} a year over the last "
                                      f"7 days ({side}). Above {_pct(FUNDING_HALVE_ANN, 0)} the rules {action} a position."))
    for r in regimes.values():
        if r.vol_label == "high":
            out.append(Tip("volatility", f"{r.symbol} swings more than on {_pct(r.vol_percentile, 0)} of days in the "
                                         f"last year ({_pct(r.vol30_ann, 0)} a year). The rules would size positions "
                                         "down automatically."))
    for r in regimes.values():
        if r.trend_label == "chop":
            out.append(Tip("chop", f"{r.symbol} is moving sideways: efficiency ratio {r.er20:.2f}, ADX {r.adx14:.0f}. "
                                   "Expect few or small losing trend trades until ER rises above 0.30."))
    above = "above" if market.btc_close > market.btc_sma200 else "below"
    direction = "rising" if market.sma200_rising else "falling"
    out.append(Tip("trend_state", f"BTC closed at {market.btc_close:,.0f}, {above} its 200-day average "
                                  f"({market.btc_sma200:,.0f}, {direction}). Market state: {market.state}."))
    if not math.isnan(market.corr_btc_eth_60d):
        out.append(Tip("correlation", f"BTC and ETH correlation over 60 days is {market.corr_btc_eth_60d:.2f}. "
                                      "The cluster risk cap treats them as (almost) one position."))
    for r in regimes.values():
        n = round(r.signal * 7)
        out.append(Tip("strategy_signal", f"For information only: the Donchian strategy would be {n}/7 long on "
                                          f"{r.symbol} today (no trades at Level 0)."))
    return out


URGENT = {"data_health", "crowding", "funding"}


def pick_tip(regimes: dict[str, SymbolRegime], market: MarketRegime, problems: list[str],
             seen: set[str], recent: list[str]) -> Tip:
    """One tip per day; first-time concepts get their explanation added."""
    options = candidates(regimes, market, problems)
    chosen = next((t for t in options if t.concept in URGENT or t.concept not in recent), options[0])
    if chosen.concept not in seen and chosen.concept in CONCEPTS:
        return Tip(chosen.concept, f"{chosen.text}\n(New concept) {CONCEPTS[chosen.concept]}")
    return chosen
