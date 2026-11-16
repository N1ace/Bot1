"""What each tactic needs from the news bot. Profiles are checked here and refused if they break a rule.

A profile counts as part of a tactic: changing one after a lab test means a new tactic and a new test. Noise_area was
tested without one, so this profile is report-only until a lab test includes it.
"""

FAMILIES = ("breakout", "mean_reversion", "trend", "post_news")
SENSITIVITY = ("block", "half", "allow")
FTMO_NEWS_MINUTES = 2  # no opening or closing either side of major news on funded Standard accounts

NOISE_AREA_DEMO = {
    "family": "breakout",
    "symbols": ["US100", "US500"],
    "event_sensitivity": {1: "block", 2: "half", 3: "allow"},
    "pre_minutes": {1: 15, 2: 10, 3: 0},
    "post_minutes": {1: 30, 2: 15, 3: 0},
    "flatten_before_tier": None,
    "situations_allowed": ["normal", "quiet", "post_event"],
    "stress_limits": {"vix_ratio_max": 1.0, "move_vs_normal_max": 2.5},
    "uses_outlook": False,
}
ACTIVE = {"noise_area": NOISE_AREA_DEMO}


def validate(name: str, profile: dict) -> None:
    if profile["family"] not in FAMILIES:
        raise ValueError(f"{name}: unknown family {profile['family']}")
    for tier, action in profile["event_sensitivity"].items():
        if action not in SENSITIVITY:
            raise ValueError(f"{name}: unknown action {action}")
        if tier <= 2 and min(profile["pre_minutes"][tier], profile["post_minutes"][tier]) < FTMO_NEWS_MINUTES:
            raise ValueError(f"{name}: tier {tier} windows are inside FTMO's news window")
    if "release" in profile["situations_allowed"]:
        raise ValueError(f"{name}: trading through a release is refused")
    if profile["uses_outlook"]:
        raise ValueError(f"{name}: outlooks aren't promoted yet")


def load_active() -> dict:
    for name, profile in ACTIVE.items():
        validate(name, profile)
    return ACTIVE
