import pytest

from leadpipe.models import Coach
from leadpipe.scoring import WEIGHTS, score_coach, tier_for


def test_booking_link_is_the_heaviest_single_signal():
    scored = score_coach(Coach(booking_url="https://calendly.com/jess/30min"))
    assert scored.breakdown == {"booking_link": 30}


def test_slot_bonus_requires_a_real_booking_link():
    # Duration alone, with no booking link, earns nothing.
    assert score_coach(Coach(booking_slot_minutes=60)).total == 0
    scored = score_coach(Coach(booking_url="https://cal.com/jess", booking_slot_minutes=60))
    assert scored.breakdown["slot_30_plus"] == 10


def test_short_slot_earns_no_duration_bonus():
    scored = score_coach(Coach(booking_url="https://cal.com/jess", booking_slot_minutes=15))
    assert "slot_30_plus" not in scored.breakdown


@pytest.mark.parametrize(
    "days,key,points",
    [(120, "ads_90_plus", 20), (90, "ads_90_plus", 20), (45, "ads_under_90", 10), (0, "ads_under_90", 10)],
)
def test_ad_longevity_bands(days, key, points):
    scored = score_coach(Coach(running_meta_ads=True, ad_days_running=days))
    assert scored.breakdown[key] == points


def test_ad_bands_are_mutually_exclusive():
    scored = score_coach(Coach(running_meta_ads=True, ad_days_running=200))
    assert "ads_under_90" not in scored.breakdown


@pytest.mark.parametrize(
    "followers,key",
    [
        (12_000, "followers_core"),
        (3_000, "followers_core"),
        (75_000, "followers_core"),
        (2_000, "followers_edge"),
        (100_000, "followers_edge"),
        (500, None),
        (400_000, None),
    ],
)
def test_follower_bands(followers, key):
    scored = score_coach(Coach(instagram_followers=followers))
    if key is None:
        assert not scored.breakdown
    else:
        assert scored.breakdown[key] == WEIGHTS[key]


def test_multi_source_presence_scores():
    scored = score_coach(Coach(source_modules=["m1_booking_serp", "m3_instagram"]))
    assert scored.breakdown["multi_source"] == 10
    # The same module twice is not corroboration.
    assert not score_coach(Coach(source_modules=["m1_booking_serp", "m1_booking_serp"])).breakdown


def test_only_a_deliverable_email_scores():
    assert score_coach(Coach(email_verify_status="deliverable")).breakdown["verified_email"] == 10
    assert not score_coach(Coach(email_verify_status="risky")).breakdown
    assert not score_coach(Coach(email_verify_status="guessed")).breakdown


def test_penalties_apply_and_score_floors_at_zero():
    coach = Coach(
        booking_url="https://calendly.com/jess/30min",
        evidence_text="Meet the team at 123 Main Street, Austin",
    )
    scored = score_coach(coach)
    assert scored.breakdown["team_language"] == -30
    assert scored.breakdown["physical_address"] == -40
    assert scored.total == 0


def test_score_is_capped_at_100():
    coach = Coach(
        booking_url="https://calendly.com/jess/45min",
        booking_slot_minutes=45,
        running_meta_ads=True,
        ad_days_running=200,
        instagram_followers=12_000,
        source_modules=["m1_booking_serp", "m2_meta_ads", "m3_instagram"],
        email_verify_status="deliverable",
        evidence_text="book a discovery call",
    )
    assert score_coach(coach).total == 100


@pytest.mark.parametrize(
    "score,tier", [(100, "outreach"), (50, "outreach"), (49, "nurture"), (30, "nurture"), (29, "discard")]
)
def test_tiers(score, tier):
    assert tier_for(score) == tier


def test_ideal_icp_record_clears_the_outreach_floor():
    """A booking link plus a long-running ad alone should qualify."""
    coach = Coach(
        booking_url="https://calendly.com/jess/30min",
        booking_slot_minutes=30,
        running_meta_ads=True,
        ad_days_running=120,
    )
    scored = score_coach(coach)
    assert scored.total == 60 and scored.tier == "outreach"
