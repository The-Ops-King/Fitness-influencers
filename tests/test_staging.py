from leadpipe.models import STATUS_RAW
from leadpipe.staging import has_identity, payload_to_coach


def test_payload_maps_to_canonical_fields():
    coach = payload_to_coach(
        {
            "full_name": "jess ryan 🏋️ | Online Coach",
            "booking_url": "https://CALENDLY.com/jess/45min?utm_source=ig",
            "booking_slot_minutes": 45,
            "emails": ["Jess@FitCo.com", "hello@fitco.com"],
            "instagram_handle": "https://instagram.com/jessfit/",
            "instagram_followers": "12000",
            "website": "http://www.jessryanfit.com/",
            "evidence_text": "1:1 online fat loss coaching. Book a discovery call.",
        },
        "m1_booking_serp",
        "https://calendly.com/jess/45min",
    )

    assert coach.full_name == "Jess Ryan"
    assert coach.booking_url == "https://calendly.com/jess/45min"
    assert coach.booking_platform == "calendly"
    assert coach.booking_slot_minutes == 45
    assert coach.primary_email == "jess@fitco.com"
    assert coach.secondary_emails == ["hello@fitco.com"]
    assert coach.instagram_handle == "jessfit"
    assert coach.instagram_followers == 12_000
    assert coach.website == "https://jessryanfit.com"
    assert coach.niche == "weight loss"
    assert coach.source_modules == ["m1_booking_serp"]
    assert coach.status == STATUS_RAW


def test_dedupe_keys_are_populated():
    coach = payload_to_coach(
        {"emails": ["Jess+ig@Gmail.com"], "website": "https://blog.jessryanfit.co.uk/x"},
        "m5_directories",
        "https://example.com/profile",
    )
    assert coach.dedupe_email == "jess@gmail.com"
    assert coach.dedupe_domain == "jessryanfit.co.uk"


def test_every_populated_field_gets_provenance():
    source = "https://instagram.com/jessfit"
    coach = payload_to_coach(
        {"instagram_handle": "jessfit", "instagram_followers": 9000}, "m3_instagram", source
    )
    assert coach.provenance["instagram_handle"] == source
    assert coach.provenance["instagram_followers"] == source
    # Absent fields must not claim a source.
    assert "booking_url" not in coach.provenance


def test_meta_ads_payload_carries_longevity():
    coach = payload_to_coach(
        {
            "business_name": "Jess Ryan Coaching",
            "website": "https://jessryanfit.com",
            "running_meta_ads": True,
            "ad_first_seen_date": "2026-01-15",
            "ad_days_running": 140,
        },
        "m2_meta_ads",
        "https://facebook.com/ads/library/?id=1",
    )
    assert coach.running_meta_ads is True
    assert coach.ad_days_running == 140
    assert coach.ad_first_seen_date.isoformat() == "2026-01-15"


def test_disqualifier_signals_are_detected_from_evidence():
    coach = payload_to_coach(
        {"website": "https://citygym.com", "evidence_text": "Meet the team at 400 Congress Ave"},
        "m5_directories",
        "https://citygym.com",
    )
    assert coach.team_language is True
    assert coach.has_physical_address is True


def test_record_with_no_key_has_no_identity():
    coach = payload_to_coach({"full_name": "Jess Ryan"}, "m6_testimonials", "https://vendor.com/x")
    assert not has_identity(coach)


def test_record_with_any_hard_key_has_identity():
    assert has_identity(payload_to_coach({"instagram_handle": "jessfit"}, "m3_instagram", "u"))
    assert has_identity(
        payload_to_coach({"booking_url": "https://cal.com/jess"}, "m1_booking_serp", "u")
    )


def test_bad_values_do_not_raise():
    coach = payload_to_coach(
        {
            "full_name": None,
            "instagram_followers": "not a number",
            "booking_url": "javascript:void(0)",
            "ad_first_seen_date": "garbage",
            "emails": ["nope", ""],
        },
        "m1_booking_serp",
        "https://example.com",
    )
    assert coach.instagram_followers is None
    assert coach.booking_url is None
    assert coach.primary_email is None
