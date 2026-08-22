import pytest

from leadpipe.filters import (
    disqualify,
    extract_price_usd,
    has_call_language,
    has_physical_address,
    has_team_language,
    is_coach_of_coaches,
)


def test_physical_address_disqualifies():
    assert has_physical_address("Visit us at 4120 Burnet Road, Austin TX")
    assert has_physical_address("Our gym hours are 6am-9pm")
    assert not has_physical_address("I coach clients over Zoom from anywhere")


def test_team_language_disqualifies():
    assert has_team_language("Meet the team behind our coaching")
    assert has_team_language("We are a full-service coaching agency")
    assert not has_team_language("I work with 12 clients at a time")


def test_coach_of_coaches_disqualifies():
    assert is_coach_of_coaches("I help online coaches scale your coaching business")
    assert is_coach_of_coaches("Join my mastermind for fitness pros")
    assert not is_coach_of_coaches("I help busy moms lose 20lbs")


def test_call_language_detection():
    assert has_call_language("Book a call with me")
    assert has_call_language("Apply to work with me 1:1")
    assert has_call_language("Grab a free consult")
    assert not has_call_language("Buy my 12 week program now")


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"text": "Come train with us at 123 Main Street"}, "physical_gym_address"),
        ({"text": "Our team of coaches"}, "team_or_agency_language"),
        ({"text": "scale your coaching business"}, "coach_of_coaches"),
        ({"text": "solo coach", "instagram_followers": 400_000}, "follower_count_above_150000"),
        (
            {"text": "Get my program", "outbound_links": ["https://buy.stripe.com/x"]},
            "checkout_no_call_step",
        ),
        # A checkout link alongside a booking link is fine - they upsell too.
        (
            {
                "text": "Get my program",
                "booking_url": "https://calendly.com/jess/30min",
                "outbound_links": ["https://buy.stripe.com/x"],
            },
            None,
        ),
        ({"text": "1:1 online coaching, book a call", "instagram_followers": 12_000}, None),
    ],
)
def test_disqualify(kwargs, expected):
    assert disqualify(**kwargs) == expected


def test_extract_price_takes_highest():
    assert extract_price_usd("From $97/mo to $1,200 per month") == 1200.0
    assert extract_price_usd("no prices here") is None
