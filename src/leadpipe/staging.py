"""Normalization pass: raw_records -> coaches.

No scraper writes to the clean table. This pass is the single writer. It
normalizes a raw payload into a Coach, applies the hard disqualifiers, resolves
the record against existing rows using the dedupe key priority, merges, records
provenance for every field, and scores.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from . import db
from .dedupe import merge, refresh_keys
from .filters import disqualify, has_physical_address, has_team_language
from .models import STATUS_RAW, STATUS_REJECTED, Coach, utcnow
from .normalize import (
    clean_person_name,
    detect_booking_platform,
    detect_niche,
    normalize_booking_url,
    normalize_email,
    normalize_instagram_handle,
    normalize_phone,
    normalize_url,
    normalize_youtube_channel,
    parse_slot_minutes,
)
from .scoring import WEIGHTS_VERSION, score_coach

log = logging.getLogger(__name__)


def payload_to_coach(payload: dict[str, Any], module: str, source_url: str | None) -> Coach:
    """Build a Coach from one module's raw payload.

    Every field that lands gets a provenance entry pointing at the URL it came
    from, so a bad value later is traceable to the scraper that produced it.
    """
    coach = Coach()
    provenance: dict[str, str] = {}
    origin = source_url or payload.get("source_url") or ""

    def put(field: str, value: Any) -> None:
        if value in (None, "", [], {}):
            return
        setattr(coach, field, value)
        if origin:
            provenance[field] = origin

    put("full_name", clean_person_name(payload.get("full_name")))
    put("business_name", _clean_text(payload.get("business_name"), 160))
    put("website", normalize_url(payload.get("website")))
    put("instagram_handle", normalize_instagram_handle(payload.get("instagram_handle")))
    put("instagram_followers", _int(payload.get("instagram_followers")))
    put("youtube_channel", normalize_youtube_channel(payload.get("youtube_channel")))
    put("facebook_page", normalize_url(payload.get("facebook_page")))
    put("linkedin_url", normalize_url(payload.get("linkedin_url")))
    put("phone", normalize_phone(payload.get("phone")))
    put("location_country", _clean_text(payload.get("location_country"), 8))
    put("location_city", _clean_text(payload.get("location_city"), 80))

    booking_url = normalize_booking_url(payload.get("booking_url"))
    if booking_url:
        put("booking_url", booking_url)
        put("booking_platform", payload.get("booking_platform") or detect_booking_platform(booking_url))
        slot = _int(payload.get("booking_slot_minutes")) or parse_slot_minutes(booking_url)
        put("booking_slot_minutes", slot)

    if payload.get("running_meta_ads"):
        put("running_meta_ads", True)
        put("ad_first_seen_date", _date(payload.get("ad_first_seen_date")))
        put("ad_days_running", _int(payload.get("ad_days_running")))

    emails = [e for e in (normalize_email(e) for e in payload.get("emails") or []) if e]
    if emails:
        put("primary_email", emails[0])
        coach.secondary_emails = emails[1:]

    evidence = _clean_text(payload.get("evidence_text"), 20_000) or ""
    coach.evidence_text = evidence
    put("niche", detect_niche(evidence, payload.get("business_name"), payload.get("full_name")))

    coach.team_language = has_team_language(evidence) or None
    coach.has_physical_address = has_physical_address(evidence) or None
    coach.source_modules = [module]
    coach.provenance = provenance
    coach.status = STATUS_RAW
    return refresh_keys(coach)


def has_identity(coach: Coach) -> bool:
    """A record with no key at all cannot be deduped or contacted."""
    return any(
        [coach.dedupe_email, coach.dedupe_booking_url, coach.dedupe_instagram, coach.dedupe_domain]
    )


def normalize_batch(rows: Iterable[dict[str, Any]], *, dry_run: bool = False) -> dict[str, int]:
    """Process staging rows into the clean table. Returns counters."""
    counters = {"processed": 0, "inserted": 0, "merged": 0, "rejected": 0, "skipped": 0, "flagged": 0}
    processed_ids: list[int] = []
    config_max_followers = _max_followers()

    for row in rows:
        raw_id = row["id"]
        module = row["source_module"]
        payload = row["payload"] or {}
        if isinstance(payload, str):
            import json

            payload = json.loads(payload)

        counters["processed"] += 1
        processed_ids.append(raw_id)

        coach = payload_to_coach(payload, module, row.get("source_url"))

        if not has_identity(coach):
            counters["skipped"] += 1
            continue

        reason = disqualify(
            text=coach.evidence_text,
            instagram_followers=coach.instagram_followers,
            booking_url=coach.booking_url,
            outbound_links=payload.get("outbound_links") or payload.get("hub_links") or [],
            max_followers=config_max_followers,
        )
        if reason:
            coach.status = STATUS_REJECTED
            coach.reject_reason = reason
            counters["rejected"] += 1

        if dry_run:
            counters["inserted"] += 1
            continue

        existing_row = db.find_existing(coach)
        if existing_row:
            existing = db.row_to_coach(existing_row)
            merge(existing, coach)
            # A previously-rejected record stays rejected unless the new
            # observation clears the disqualifier that flagged it.
            if coach.status == STATUS_REJECTED and existing.status != STATUS_REJECTED:
                existing.status = STATUS_REJECTED
                existing.reject_reason = coach.reject_reason
            _finalize(existing, module, raw_id)
            counters["merged"] += 1
            continue

        # Nothing matched on a hard key. Park any close name match for a human
        # rather than guessing - two coaches really can share a name.
        candidates = db.fuzzy_candidates(coach)
        coach_id = _finalize(coach, module, raw_id)
        counters["inserted"] += 1
        for candidate in candidates:
            db.flag_merge_candidate(
                coach_id, int(candidate["id"]), float(candidate.get("sim", 0.0)), "fuzzy_name_niche"
            )
            counters["flagged"] += 1

    if not dry_run:
        db.mark_raw_processed(processed_ids)
    return counters


def _finalize(coach: Coach, module: str, raw_id: int | None) -> int:
    """Score, persist, and log provenance. Returns the coach id."""
    result = score_coach(coach)
    coach.qualification_score = result.total
    refresh_keys(coach)
    coach_id = db.upsert_coach(coach)
    db.record_provenance(coach_id, coach.provenance, module, raw_id)
    db.record_score(coach_id, result.total, result.breakdown, WEIGHTS_VERSION)
    return coach_id


def run_normalization(batch_size: int = 1000, module: str | None = None, dry_run: bool = False) -> dict[str, int]:
    """Drain staging in batches until nothing unprocessed remains."""
    totals = {"processed": 0, "inserted": 0, "merged": 0, "rejected": 0, "skipped": 0, "flagged": 0}
    while True:
        rows = db.fetch_unprocessed_raw(limit=batch_size, module=module)
        if not rows:
            break
        counters = normalize_batch(rows, dry_run=dry_run)
        for key, value in counters.items():
            totals[key] += value
        log.info("normalized batch: %s", counters)
        if dry_run:
            break
    return totals


def _finalize_existing(coach: Coach) -> None:
    """Re-score and persist an already-stored coach (used by the enrich pass)."""
    result = score_coach(coach)
    coach.qualification_score = result.total
    coach.last_verified_at = utcnow()
    refresh_keys(coach)
    coach_id = db.upsert_coach(coach)
    db.record_score(coach_id, result.total, result.breakdown, WEIGHTS_VERSION)


def _clean_text(value: Any, limit: int) -> str | None:
    if not value or not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned[:limit] or None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _date(value: Any):
    from datetime import date, datetime

    if isinstance(value, date):
        return value
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _max_followers() -> int:
    from .config import get_config

    return get_config().max_followers
