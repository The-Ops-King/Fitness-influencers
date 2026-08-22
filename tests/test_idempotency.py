"""Re-run idempotency: a weekly run must not duplicate records."""

from leadpipe.models import RawRecord


def test_identical_observations_hash_identically():
    a = RawRecord(source_module="m1_booking_serp", payload={"booking_url": "https://cal.com/j"})
    b = RawRecord(source_module="m1_booking_serp", payload={"booking_url": "https://cal.com/j"})
    assert a.content_hash() == b.content_hash()


def test_key_order_does_not_change_the_hash():
    a = RawRecord(source_module="m1", payload={"a": 1, "b": 2})
    b = RawRecord(source_module="m1", payload={"b": 2, "a": 1})
    assert a.content_hash() == b.content_hash()


def test_volatile_keys_are_excluded_from_the_hash():
    """An unchanged page must not produce a new row just because time passed."""
    a = RawRecord(source_module="m1", payload={"url": "x", "scraped_at": "2026-01-01"})
    b = RawRecord(source_module="m1", payload={"url": "x", "scraped_at": "2026-08-22"})
    assert a.content_hash() == b.content_hash()


def test_different_content_hashes_differently():
    a = RawRecord(source_module="m1", payload={"url": "x"})
    b = RawRecord(source_module="m1", payload={"url": "y"})
    assert a.content_hash() != b.content_hash()


def test_the_same_payload_from_two_modules_is_two_observations():
    a = RawRecord(source_module="m1_booking_serp", payload={"url": "x"})
    b = RawRecord(source_module="m3_instagram", payload={"url": "x"})
    assert a.content_hash() != b.content_hash()


def test_nested_payloads_hash_stably():
    a = RawRecord(source_module="m1", payload={"links": ["a", "b"], "meta": {"x": 1, "y": 2}})
    b = RawRecord(source_module="m1", payload={"meta": {"y": 2, "x": 1}, "links": ["a", "b"]})
    assert a.content_hash() == b.content_hash()
