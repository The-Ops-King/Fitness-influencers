"""Enrichment pass logic, with the network stubbed."""

import leadpipe.pipeline as pipeline
from leadpipe.enrich.booking import BookingPageFacts
from leadpipe.enrich.email_verify import VerifyResult
from leadpipe.enrich.site_scrape import SiteFacts
from leadpipe.models import STATUS_REJECTED, STATUS_VERIFIED, Coach


def stub(monkeypatch, *, site=None, booking=None, verify=None, guesses=None):
    monkeypatch.setattr(pipeline, "scrape_site", site or (lambda url, client=None, max_pages=4: SiteFacts(url=url)))
    monkeypatch.setattr(pipeline, "inspect_booking_page", booking or (lambda url, client=None: BookingPageFacts(url=url)))
    monkeypatch.setattr(pipeline, "verify_email", verify or (lambda e, client=None: VerifyResult(e, "unknown", "stub")))
    monkeypatch.setattr(pipeline, "guess_emails", guesses or (lambda name, site, limit=6: []))


def test_a_dead_booking_link_stops_scoring(monkeypatch):
    """A link that no longer resolves must not keep earning 30 points."""
    stub(monkeypatch, booking=lambda url, client=None: BookingPageFacts(url=url, reachable=False))
    coach = Coach(booking_url="https://calendly.com/gone/30min", booking_platform="calendly")
    pipeline.enrich_coach(coach, None)
    assert coach.booking_url is None and coach.booking_platform is None


def test_a_live_booking_link_updates_slot_and_owner(monkeypatch):
    stub(
        monkeypatch,
        booking=lambda url, client=None: BookingPageFacts(
            url=url, reachable=True, slot_minutes=45, owner_name="Jess Ryan"
        ),
    )
    coach = Coach(booking_url="https://calendly.com/jess/45min")
    pipeline.enrich_coach(coach, None)
    assert coach.booking_slot_minutes == 45
    assert coach.full_name == "Jess Ryan"


def test_site_scrape_normalizes_a_discovered_phone(monkeypatch):
    stub(
        monkeypatch,
        site=lambda url, client=None, max_pages=4: SiteFacts(
            url=url, reachable=True, phones=["(512) 555-0134"], text="online coaching"
        ),
    )
    coach = Coach(website="https://jessryanfit.com")
    pipeline.enrich_coach(coach, None)
    assert coach.phone == "+15125550134"


def test_hard_bounces_are_dropped_before_they_reach_a_sender(monkeypatch):
    stub(monkeypatch, verify=lambda e, client=None: VerifyResult(e, "undeliverable", "stub"))
    coach = Coach(primary_email="dead@fitco.com", secondary_emails=["also-dead@fitco.com"])
    pipeline.enrich_coach(coach, None)
    assert coach.primary_email is None
    assert coach.secondary_emails == []
    assert coach.email_verify_status == "undeliverable"


def test_a_deliverable_address_is_promoted_to_primary(monkeypatch):
    def verify(email, client=None):
        return VerifyResult(email, "deliverable" if email.startswith("hello@") else "risky", "stub")

    stub(monkeypatch, verify=verify)
    coach = Coach(primary_email="risky@fitco.com", secondary_emails=["hello@fitco.com"])
    pipeline.enrich_coach(coach, None)
    assert coach.primary_email == "hello@fitco.com"
    assert coach.email_verify_status == "deliverable"
    assert coach.status == STATUS_VERIFIED


def test_a_guessed_address_is_only_used_once_it_verifies(monkeypatch):
    stub(
        monkeypatch,
        guesses=lambda name, site, limit=6: ["jess@jessryanfit.com"],
        verify=lambda e, client=None: VerifyResult(e, "unknown", "stub"),
    )
    coach = Coach(full_name="Jess Ryan", website="https://jessryanfit.com")
    pipeline.enrich_coach(coach, None)
    # Unverified guess: never promoted.
    assert coach.primary_email is None
    assert coach.email_verify_status == "guessed"

    stub(
        monkeypatch,
        guesses=lambda name, site, limit=6: ["jess@jessryanfit.com"],
        verify=lambda e, client=None: VerifyResult(e, "deliverable", "stub"),
    )
    coach = Coach(full_name="Jess Ryan", website="https://jessryanfit.com")
    pipeline.enrich_coach(coach, None)
    assert coach.primary_email == "jess@jessryanfit.com"


def test_a_disqualifier_found_during_enrichment_rejects_the_record(monkeypatch):
    stub(
        monkeypatch,
        site=lambda url, client=None, max_pages=4: SiteFacts(
            url=url, reachable=True, text="Meet the team. We are a full-service coaching agency."
        ),
    )
    coach = Coach(website="https://apexcollective.com")
    pipeline.enrich_coach(coach, None)
    assert coach.status == STATUS_REJECTED
    assert coach.reject_reason == "team_or_agency_language"
