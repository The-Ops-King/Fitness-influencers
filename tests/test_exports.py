import csv

import pytest

from leadpipe.exports import (
    export_cold_email,
    export_instagram_dm,
    export_meta_audience,
    personalization_detail,
)


def read(path):
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


BASE = {
    "primary_email": "jess@fitco.com",
    "email_verify_status": "deliverable",
    "qualification_score": 85,
    "full_name": "Jess Ryan",
    "business_name": "Jess Ryan Coaching",
    "niche": "weight loss",
    "booking_platform": "calendly",
    "booking_url": "https://calendly.com/jess/30min",
    "website": "https://jessryanfit.com",
    "instagram_handle": "jessfit",
    "instagram_followers": 12_000,
    "running_meta_ads": True,
    "ad_days_running": 140,
    "phone": "(512) 555-0134",
    "location_country": "US",
    "status": "verified",
    "source_modules": ["m1_booking_serp", "m3_instagram"],
}


def test_cold_email_excludes_unverified_addresses(tmp_path):
    rows = [BASE, {**BASE, "primary_email": "risky@fitco.com", "email_verify_status": "risky"}]
    result = export_cold_email(rows, tmp_path / "cold.csv")
    written = read(result.path)
    assert len(written) == 1
    assert written[0]["email"] == "jess@fitco.com"


def test_cold_email_respects_score_floor(tmp_path):
    rows = [{**BASE, "qualification_score": 49}]
    assert export_cold_email(rows, tmp_path / "cold.csv", min_score=50).rows == 0


def test_cold_email_carries_merge_fields(tmp_path):
    row = read(export_cold_email([BASE], tmp_path / "cold.csv").path)[0]
    assert row["first_name"] == "Jess"
    assert row["niche"] == "weight loss"
    assert row["booking_platform"] == "calendly"
    assert row["personalization_detail"] == "running Meta ads for 140 days"
    assert row["source_modules"] == "m1_booking_serp|m3_instagram"


def test_instagram_dm_needs_no_verified_email(tmp_path):
    rows = [{**BASE, "primary_email": None, "email_verify_status": "unknown", "qualification_score": 45}]
    written = read(export_instagram_dm(rows, tmp_path / "dm.csv").path)
    assert len(written) == 1 and written[0]["instagram_handle"] == "jessfit"


def test_instagram_dm_dedupes_handles(tmp_path):
    rows = [BASE, {**BASE, "instagram_handle": "@JessFit"}]
    assert export_instagram_dm(rows, tmp_path / "dm.csv").rows == 1


def test_instagram_dm_floor_is_lower_than_email(tmp_path):
    rows = [{**BASE, "qualification_score": 40}]
    assert export_instagram_dm(rows, tmp_path / "dm.csv", min_score=40).rows == 1
    assert export_cold_email(rows, tmp_path / "cold.csv", min_score=50).rows == 0


def test_meta_audience_is_a_narrower_cut(tmp_path):
    rows = [{**BASE, "qualification_score": 55}]
    assert export_meta_audience(rows, tmp_path / "meta.csv", min_score=70).rows == 0
    assert export_meta_audience(rows, tmp_path / "meta.csv", min_score=50).rows == 1


def test_meta_audience_columns_and_hashing(tmp_path):
    written = read(export_meta_audience([BASE], tmp_path / "meta.csv", min_score=50).path)
    assert list(written[0]) == ["email", "phone", "fn", "ln", "country"]
    assert len(written[0]["email"]) == 64  # sha256 hex
    assert written[0]["country"] == "us"

    plain = read(
        export_meta_audience([BASE], tmp_path / "plain.csv", min_score=50, hash_values=False).path
    )
    assert plain[0]["email"] == "jess@fitco.com"
    assert plain[0]["phone"] == "15125550134"


def test_meta_audience_skips_records_with_no_contact(tmp_path):
    rows = [{**BASE, "primary_email": None, "phone": None}]
    assert export_meta_audience(rows, tmp_path / "meta.csv", min_score=50).rows == 0


def test_rejected_records_never_export(tmp_path):
    rows = [{**BASE, "status": "rejected"}]
    assert export_cold_email(rows, tmp_path / "a.csv").rows == 0
    assert export_instagram_dm(rows, tmp_path / "b.csv").rows == 0
    assert export_meta_audience(rows, tmp_path / "c.csv", min_score=50).rows == 0


@pytest.mark.parametrize(
    "row,expected",
    [
        ({"running_meta_ads": True, "ad_days_running": 90}, "running Meta ads for 90 days"),
        ({"booking_slot_minutes": 45, "booking_platform": "cal.com"}, "45-minute call on cal.com"),
        ({"instagram_followers": 8500}, "8,500 Instagram followers"),
        ({"niche": "prenatal"}, "prenatal coaching"),
        ({}, ""),
    ],
)
def test_personalization_detail_prefers_the_sharpest_signal(row, expected):
    assert personalization_detail(row) == expected


def test_unverified_addresses_are_reported_not_dropped_silently(tmp_path, caplog):
    """An empty cold-email CSV usually means no verifier is configured."""
    import logging

    rows = [{**BASE, "email_verify_status": "unknown"}]
    with caplog.at_level(logging.WARNING, logger="leadpipe.exports"):
        result = export_cold_email(rows, tmp_path / "cold.csv")
    assert result.rows == 0
    assert "not verified deliverable" in caplog.text


# -- Instagram DM: the primary export under an Instagram-first ICP ----------


def test_dm_export_carries_everything_needed_to_open_a_conversation(tmp_path):
    row = read(export_instagram_dm([BASE], tmp_path / "dm.csv").path)[0]
    assert row["profile_url"] == "https://instagram.com/jessfit"
    assert row["business_name"] == "Jess Ryan Coaching"
    assert row["booking_platform"] == "calendly"
    assert row["website"] == "https://jessryanfit.com"
    assert row["running_meta_ads"] == "yes"
    assert row["ad_days_running"] == "140"
    assert row["source_modules"] == "m1_booking_serp|m3_instagram"


def test_dm_export_is_sorted_by_score_descending(tmp_path):
    rows = [
        {**BASE, "instagram_handle": "low", "qualification_score": 45},
        {**BASE, "instagram_handle": "high", "qualification_score": 95},
        {**BASE, "instagram_handle": "mid", "qualification_score": 70},
    ]
    written = read(export_instagram_dm(rows, tmp_path / "dm.csv").path)
    assert [r["instagram_handle"] for r in written] == ["high", "mid", "low"]


def test_dm_export_works_without_follower_data(tmp_path):
    """The free path has no follower counts; the export must not require them."""
    rows = [{**BASE, "instagram_followers": None}]
    written = read(export_instagram_dm(rows, tmp_path / "dm.csv").path)
    assert len(written) == 1 and written[0]["followers"] == ""


def test_a_record_with_no_handle_never_reaches_the_dm_list(tmp_path):
    rows = [{**BASE, "instagram_handle": None, "qualification_score": 100}]
    assert export_instagram_dm(rows, tmp_path / "dm.csv").rows == 0
