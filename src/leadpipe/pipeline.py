"""Enrichment and re-scoring passes over the clean table."""

from __future__ import annotations

import logging
from collections.abc import Iterator

from . import db
from .dedupe import refresh_keys
from .enrich.booking import inspect_booking_page
from .enrich.email_guess import guess_emails
from .enrich.email_verify import pick_primary, verify_email
from .enrich.site_scrape import scrape_site
from .filters import disqualify
from .http import HttpClient
from .models import STATUS_ENRICHED, STATUS_REJECTED, STATUS_VERIFIED, Coach, utcnow
from .normalize import is_booking_url, normalize_phone
from .scoring import WEIGHTS_VERSION, score_coach

log = logging.getLogger(__name__)


def enrich_coach(coach: Coach, web: HttpClient) -> Coach:
    """Site scrape, booking re-check, email discovery and verification."""
    provenance: dict[str, str] = {}

    if coach.website:
        site = scrape_site(coach.website, client=web)
        if site.reachable:
            coach.evidence_text = "\n".join(p for p in (coach.evidence_text, site.text[:8000]) if p)
            coach.team_language = coach.team_language or site.team_language
            coach.has_physical_address = coach.has_physical_address or site.physical_address
            if site.price_usd is not None:
                coach.price_point_usd = site.price_usd
            if not coach.booking_url and site.booking_urls:
                coach.booking_url = site.booking_urls[0]
                provenance["booking_url"] = coach.website
            if not coach.instagram_handle and site.instagram_handle:
                coach.instagram_handle = site.instagram_handle
                provenance["instagram_handle"] = coach.website
            if not coach.phone and site.phones:
                # Normalize here too: the Meta audience export hashes this value,
                # so an unnormalized string would never match.
                coach.phone = normalize_phone(site.phones[0])
                provenance["phone"] = coach.website
            for email in site.emails:
                if email not in coach.secondary_emails and email != coach.primary_email:
                    coach.secondary_emails.append(email)

    # Confirm the booking link still resolves. A dead link should stop scoring
    # 30 points on next week's run.
    if coach.booking_url and is_booking_url(coach.booking_url):
        facts = inspect_booking_page(coach.booking_url, client=web)
        if facts.reachable:
            if facts.slot_minutes:
                coach.booking_slot_minutes = facts.slot_minutes
            if not coach.full_name and facts.owner_name:
                coach.full_name = facts.owner_name
                provenance["full_name"] = coach.booking_url
        else:
            coach.booking_url = None
            coach.booking_platform = None

    _resolve_email(coach, provenance)

    reason = disqualify(
        text=coach.evidence_text,
        instagram_followers=coach.instagram_followers,
        booking_url=coach.booking_url,
    )
    if reason:
        coach.status = STATUS_REJECTED
        coach.reject_reason = reason
    elif coach.email_verify_status == "deliverable":
        coach.status = STATUS_VERIFIED
    else:
        coach.status = STATUS_ENRICHED

    coach.last_verified_at = utcnow()
    coach.provenance.update(provenance)
    return coach


def _resolve_email(coach: Coach, provenance: dict[str, str]) -> None:
    """Find a deliverable address, guessing from the domain only if needed.

    Guesses are verified before they are ever promoted to primary_email. An
    unverified guess never reaches a sending domain.
    """
    candidates = [e for e in [coach.primary_email, *coach.secondary_emails] if e]

    if not candidates and coach.website:
        guesses = guess_emails(coach.full_name, coach.website)
        results = [verify_email(g) for g in guesses]
        best = pick_primary([r for r in results if r.status == "deliverable"])
        if best:
            coach.primary_email = best.email
            coach.email_verify_status = "deliverable"
            provenance["primary_email"] = f"pattern_guess:{coach.website}"
        else:
            # Record that guessing was attempted so it is not retried weekly.
            coach.email_verify_status = coach.email_verify_status or "guessed"
        return

    results = [verify_email(e) for e in candidates[:5]]
    deliverable = [r for r in results if r.status == "deliverable"]
    best = pick_primary(deliverable) or pick_primary(results)

    if best:
        coach.primary_email = best.email
        coach.email_verify_status = best.status
        coach.secondary_emails = [
            r.email for r in results if r.email != best.email and r.status != "undeliverable"
        ]
    else:
        # Every candidate hard-bounced. Drop them before they touch a sender.
        coach.primary_email = None
        coach.secondary_emails = []
        coach.email_verify_status = "undeliverable"


def run_enrichment(limit: int | None = None, statuses: tuple[str, ...] = ("raw",)) -> dict[str, int]:
    """Enrich coaches in the given statuses."""
    web = HttpClient("web")
    counters = {"enriched": 0, "verified": 0, "rejected": 0}
    placeholders = ", ".join(["%s"] * len(statuses))

    for processed, row in enumerate(db.iter_coaches(f"status IN ({placeholders})", list(statuses)), start=1):
        coach = db.row_to_coach(row)
        coach.evidence_text = ""
        enrich_coach(coach, web)

        result = score_coach(coach)
        coach.qualification_score = result.total
        refresh_keys(coach)
        db.upsert_coach(coach)
        db.record_provenance(coach.id or 0, coach.provenance, "enrichment", None)
        db.record_score(coach.id or 0, result.total, result.breakdown, WEIGHTS_VERSION)

        if coach.status == STATUS_REJECTED:
            counters["rejected"] += 1
        elif coach.status == STATUS_VERIFIED:
            counters["verified"] += 1
        else:
            counters["enriched"] += 1

        if limit and processed >= limit:
            break

    return counters


def run_rescore(where: str = "status <> 'rejected'") -> dict[str, int]:
    """Re-apply the current weights to stored records. Cheap; no network."""
    counters = {"rescored": 0, "outreach": 0, "nurture": 0}
    for row in db.iter_coaches(where):
        coach = db.row_to_coach(row)
        coach.evidence_text = ""
        result = score_coach(coach)
        coach.qualification_score = result.total
        db.upsert_coach(coach)
        db.record_score(coach.id or 0, result.total, result.breakdown, WEIGHTS_VERSION)
        counters["rescored"] += 1
        if result.tier == "outreach":
            counters["outreach"] += 1
        elif result.tier == "nurture":
            counters["nurture"] += 1
    return counters


def iter_scored(min_score: int, extra_where: str = "TRUE", params: tuple | list = ()) -> Iterator[dict]:
    return db.iter_coaches(
        f"status <> 'rejected' AND qualification_score >= %s AND ({extra_where})",
        [min_score, *params],
    )
