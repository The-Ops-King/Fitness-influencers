"""Qualification scoring.

Weights live in one dict so a tuning pass after the first run is a single edit.
`WEIGHTS_VERSION` is stamped onto every score_events row, so a re-score can be
compared against the previous weighting instead of silently overwriting it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .filters import has_call_language, has_physical_address, has_team_language
from .models import Coach
from .normalize import is_booking_url

# v2: Instagram-first. Instagram DM is the primary outreach channel, so having
# a handle is worth points in its own right and a verified email is a bonus
# rather than the main event. v1 weights are preserved in git history, and every
# score_events row is stamped with the version that produced it, so the two are
# still comparable after a `leadpipe rescore`.
WEIGHTS_VERSION = "v2"

WEIGHTS: dict[str, int] = {
    "booking_link": 30,
    "slot_30_plus": 10,
    "ads_90_plus": 20,
    "ads_under_90": 10,
    # The contact channel. Without it there is no way to reach this person,
    # however well they otherwise fit the ICP.
    "instagram_handle": 15,
    "followers_core": 15,      # 3K-75K
    "followers_edge": 7,       # 1K-3K or 75K-150K
    "multi_source": 10,        # found by 2+ modules
    # Was 10 under v1's email-first weighting. Still useful as a second channel,
    # but no longer something a record should be penalised for lacking.
    "verified_email": 3,
    "call_language": 5,
    "team_language": -30,
    "physical_address": -40,
}

OUTREACH_FLOOR = 50
NURTURE_FLOOR = 30

DELIVERABLE_STATUSES = {"deliverable", "valid", "ok"}


@dataclass
class ScoreResult:
    total: int
    breakdown: dict[str, int]
    tier: str  # outreach | nurture | discard

    def as_payload(self) -> dict[str, Any]:
        return {"total": self.total, "breakdown": self.breakdown, "tier": self.tier}


def tier_for(score: int, *, outreach_floor: int = OUTREACH_FLOOR, nurture_floor: int = NURTURE_FLOOR) -> str:
    if score >= outreach_floor:
        return "outreach"
    if score >= nurture_floor:
        return "nurture"
    return "discard"


def score_coach(
    coach: Coach,
    *,
    outreach_floor: int = OUTREACH_FLOOR,
    nurture_floor: int = NURTURE_FLOOR,
) -> ScoreResult:
    """Score a coach 0-100. Bands are mutually exclusive within each signal."""
    breakdown: dict[str, int] = {}

    # The single strongest proxy in the whole pipeline.
    if is_booking_url(coach.booking_url):
        breakdown["booking_link"] = WEIGHTS["booking_link"]
        # Duration only means something once there is a real booking link.
        if coach.booking_slot_minutes is not None and coach.booking_slot_minutes >= 30:
            breakdown["slot_30_plus"] = WEIGHTS["slot_30_plus"]

    # Ad longevity is the best revenue proxy available. An ad running 90+ days
    # is one someone is choosing to keep paying for.
    if coach.running_meta_ads:
        days = coach.ad_days_running or 0
        if days >= 90:
            breakdown["ads_90_plus"] = WEIGHTS["ads_90_plus"]
        else:
            breakdown["ads_under_90"] = WEIGHTS["ads_under_90"]

    if coach.instagram_handle:
        breakdown["instagram_handle"] = WEIGHTS["instagram_handle"]

    # Follower data is only available when module 3 has run. Its absence must
    # not depress the score of an otherwise reachable coach, so there is no
    # penalty here - the bands are a bonus when the data exists.
    followers = coach.instagram_followers
    if followers is not None:
        if 3_000 <= followers <= 75_000:
            breakdown["followers_core"] = WEIGHTS["followers_core"]
        elif 1_000 <= followers < 3_000 or 75_000 < followers <= 150_000:
            breakdown["followers_edge"] = WEIGHTS["followers_edge"]

    # Corroboration across independent modules is itself a quality signal.
    if len(set(coach.source_modules)) >= 2:
        breakdown["multi_source"] = WEIGHTS["multi_source"]

    if (coach.email_verify_status or "").lower() in DELIVERABLE_STATUSES:
        breakdown["verified_email"] = WEIGHTS["verified_email"]

    evidence = coach.evidence_text or ""
    if has_call_language(evidence):
        breakdown["call_language"] = WEIGHTS["call_language"]

    if coach.team_language or has_team_language(evidence):
        breakdown["team_language"] = WEIGHTS["team_language"]

    if coach.has_physical_address or has_physical_address(evidence):
        breakdown["physical_address"] = WEIGHTS["physical_address"]

    total = max(0, min(100, sum(breakdown.values())))
    return ScoreResult(
        total=total,
        breakdown=breakdown,
        tier=tier_for(total, outreach_floor=outreach_floor, nurture_floor=nurture_floor),
    )
