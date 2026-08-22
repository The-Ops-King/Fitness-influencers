from datetime import UTC

from leadpipe.dedupe import dedupe_batch, find_match, match, merge, refresh_keys
from leadpipe.models import Coach


def make(**kwargs) -> Coach:
    return refresh_keys(Coach(**kwargs))


def test_matches_on_exact_email():
    a = make(primary_email="Jess@FitCo.com")
    b = make(primary_email="jess@fitco.com", full_name="Jess Ryan")
    result = match(a, b)
    assert result.matched and result.strategy == "email"


def test_matches_on_booking_url_ignoring_query_params():
    a = make(booking_url="https://calendly.com/jess/30min?utm_source=ig")
    b = make(booking_url="https://calendly.com/jess/30min?month=2026-02")
    assert match(a, b).strategy == "booking_url"


def test_matches_on_instagram_handle():
    a = make(instagram_handle="@JessFit")
    b = make(instagram_handle="https://instagram.com/jessfit/")
    assert match(a, b).strategy == "instagram"


def test_matches_on_root_domain():
    a = make(website="https://www.jessryanfit.com/coaching")
    b = make(website="https://jessryanfit.com/about")
    assert match(a, b).strategy == "domain"


def test_fuzzy_name_match_requires_same_niche_and_is_flagged():
    a = make(full_name="Jessica Ryan", niche="weight loss")
    b = make(full_name="Jessica Ryan", niche="weight loss")
    result = match(a, b)
    assert result.matched and result.strategy == "fuzzy_name" and result.needs_review

    c = make(full_name="Jessica Ryan", niche="strength")
    assert not match(a, c).matched


def test_no_match_on_empty_records():
    assert not match(make(), make()).matched


def test_match_priority_prefers_email_over_fuzzy_name():
    candidate = make(primary_email="jess@fitco.com", full_name="Jess Ryan", niche="strength")
    fuzzy_only = make(full_name="Jess Ryan", niche="strength")
    email_row = make(primary_email="jess@fitco.com")
    found, result = find_match(candidate, [fuzzy_only, email_row])
    assert found is email_row and result.strategy == "email"


def test_merge_unions_source_modules_and_keeps_earliest_first_seen():
    from datetime import datetime

    early = datetime(2026, 1, 1, tzinfo=UTC)
    late = datetime(2026, 6, 1, tzinfo=UTC)
    base = make(source_modules=["m1_booking_serp"], first_seen_at=late)
    other = make(source_modules=["m3_instagram"], first_seen_at=early)

    merge(base, other)
    assert base.source_modules == ["m1_booking_serp", "m3_instagram"]
    assert base.first_seen_at == early


def test_merge_never_overwrites_a_value_with_null():
    base = make(full_name="Jess Ryan", instagram_followers=12_000)
    merge(base, make(full_name=None, instagram_followers=None))
    assert base.full_name == "Jess Ryan"
    assert base.instagram_followers == 12_000


def test_merge_takes_max_of_monotonic_counters():
    base = make(instagram_followers=9_000, ad_days_running=30)
    merge(base, make(instagram_followers=12_000, ad_days_running=120))
    assert base.instagram_followers == 12_000
    assert base.ad_days_running == 120


def test_merge_demotes_a_second_email_to_secondary():
    base = make(primary_email="jess@fitco.com")
    merge(base, make(primary_email="hello@fitco.com"))
    assert base.primary_email == "jess@fitco.com"
    assert base.secondary_emails == ["hello@fitco.com"]


def test_merge_ors_boolean_signals():
    base = make(running_meta_ads=False, team_language=False)
    merge(base, make(running_meta_ads=True, team_language=True))
    assert base.running_meta_ads is True
    assert base.team_language is True


def test_dedupe_batch_collapses_across_modules():
    records = [
        make(booking_url="https://calendly.com/jess/30min?a=1", source_modules=["m1_booking_serp"]),
        make(
            booking_url="https://calendly.com/jess/30min",
            instagram_handle="jessfit",
            source_modules=["m3_instagram"],
        ),
        make(instagram_handle="mikelifts", source_modules=["m3_instagram"]),
    ]
    out = dedupe_batch(records)
    assert len(out) == 2
    assert out[0].source_modules == ["m1_booking_serp", "m3_instagram"]
    assert out[0].instagram_handle == "jessfit"
