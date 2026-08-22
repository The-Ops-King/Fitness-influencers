"""Deduplication and merge.

Match priority, highest confidence first:
  1. exact email
  2. normalized booking URL (query stripped)
  3. instagram handle
  4. normalized root domain of website
  5. fuzzy name + niche above 0.9 -> flagged, never auto-merged
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date, datetime

from .models import Coach
from .normalize import (
    email_dedupe_key,
    name_key,
    name_similarity,
    normalize_booking_url,
    normalize_instagram_handle,
    root_domain,
)

FUZZY_THRESHOLD = 0.9

MATCH_PRIORITY = ("email", "booking_url", "instagram", "domain", "fuzzy_name")


@dataclass(frozen=True)
class MatchResult:
    matched: bool
    strategy: str | None = None
    similarity: float = 0.0
    needs_review: bool = False


def refresh_keys(coach: Coach) -> Coach:
    """Recompute the dedupe keys from the current field values."""
    coach.dedupe_email = email_dedupe_key(coach.primary_email)
    coach.dedupe_booking_url = normalize_booking_url(coach.booking_url)
    coach.dedupe_instagram = normalize_instagram_handle(coach.instagram_handle)
    coach.dedupe_domain = root_domain(coach.website)
    coach.dedupe_name_key = name_key(coach.full_name)
    return coach


def match(a: Coach, b: Coach, *, threshold: float = FUZZY_THRESHOLD) -> MatchResult:
    """Decide whether two records are the same person."""
    refresh_keys(a)
    refresh_keys(b)

    if a.dedupe_email and a.dedupe_email == b.dedupe_email:
        return MatchResult(True, "email", 1.0)

    if a.dedupe_booking_url and a.dedupe_booking_url == b.dedupe_booking_url:
        return MatchResult(True, "booking_url", 1.0)

    if a.dedupe_instagram and a.dedupe_instagram == b.dedupe_instagram:
        return MatchResult(True, "instagram", 1.0)

    if a.dedupe_domain and a.dedupe_domain == b.dedupe_domain:
        return MatchResult(True, "domain", 1.0)

    # Fuzzy name is only credible when the niche agrees too, and even then a
    # human confirms it. Two "Sarah Miller" weight-loss coaches do exist.
    if a.dedupe_name_key and b.dedupe_name_key and a.niche and a.niche == b.niche:
        similarity = name_similarity(a.full_name, b.full_name)
        if similarity >= threshold:
            return MatchResult(True, "fuzzy_name", similarity, needs_review=True)

    return MatchResult(False)


def find_match(candidate: Coach, existing: list[Coach], *, threshold: float = FUZZY_THRESHOLD) -> tuple[Coach | None, MatchResult]:
    """Best match for `candidate` among `existing`, honouring match priority."""
    best: tuple[Coach, MatchResult] | None = None
    for other in existing:
        result = match(candidate, other, threshold=threshold)
        if not result.matched:
            continue
        if best is None or MATCH_PRIORITY.index(result.strategy or "fuzzy_name") < MATCH_PRIORITY.index(
            best[1].strategy or "fuzzy_name"
        ):
            best = (other, result)
    if best is None:
        return None, MatchResult(False)
    return best[0], best[1]


# Fields where a later observation should not clobber an earlier confirmed one.
_MAX_FIELDS = {"instagram_followers", "ad_days_running", "booking_slot_minutes", "qualification_score"}
_MIN_DATE_FIELDS = {"first_seen_at", "ad_first_seen_date"}
_MAX_DATE_FIELDS = {"last_verified_at"}
_SKIP_FIELDS = {"id", "source_modules", "secondary_emails", "provenance", "evidence_text", "status"}


def merge(base: Coach, incoming: Coach, *, review_reason: str | None = None) -> Coach:
    """Fold `incoming` into `base` in place and return it.

    Rules: never overwrite a value with null; prefer the earliest first_seen and
    the latest verification; take the max of monotonic counters; union the
    source_modules array, since multi-source presence is a quality signal.
    """
    for f in fields(Coach):
        name = f.name
        if name in _SKIP_FIELDS:
            continue
        new = getattr(incoming, name)
        old = getattr(base, name)

        if new is None or new == "" or new == []:
            continue
        if old is None or old == "" or old == []:
            setattr(base, name, new)
            continue

        if name in _MAX_FIELDS and isinstance(new, int) and isinstance(old, int):
            setattr(base, name, max(old, new))
        elif name in _MIN_DATE_FIELDS and isinstance(new, (date, datetime)) and isinstance(old, (date, datetime)):
            setattr(base, name, min(old, new))
        elif name in _MAX_DATE_FIELDS and isinstance(new, (date, datetime)) and isinstance(old, (date, datetime)):
            setattr(base, name, max(old, new))
        elif name == "running_meta_ads" or name in {"team_language", "has_physical_address", "needs_manual_review"}:
            setattr(base, name, bool(old or new))
        # Otherwise keep the incumbent value: first confirmed observation wins.

    # Union of modules. Order-stable so exports stay diff-friendly.
    merged_modules = list(dict.fromkeys([*base.source_modules, *incoming.source_modules]))
    base.source_modules = merged_modules

    # An email that arrives second becomes a secondary, never silently dropped.
    secondaries = dict.fromkeys([*base.secondary_emails, *incoming.secondary_emails])
    if incoming.primary_email and incoming.primary_email != base.primary_email:
        secondaries.setdefault(incoming.primary_email, None)
    secondaries.pop(base.primary_email, None)
    base.secondary_emails = [e for e in secondaries if e]

    base.evidence_text = "\n".join(
        part for part in dict.fromkeys([base.evidence_text, incoming.evidence_text]) if part
    ).strip()

    for field_name, source_url in incoming.provenance.items():
        base.provenance.setdefault(field_name, source_url)

    if review_reason:
        base.needs_manual_review = True
        base.review_reason = review_reason

    return refresh_keys(base)


def dedupe_batch(records: list[Coach], *, threshold: float = FUZZY_THRESHOLD) -> list[Coach]:
    """Collapse a batch of records in memory. Used by the normalization pass."""
    out: list[Coach] = []
    for record in records:
        refresh_keys(record)
        existing, result = find_match(record, out, threshold=threshold)
        if existing is None:
            out.append(record)
            continue
        reason = (
            f"fuzzy_name_match:{result.similarity:.2f}" if result.needs_review else None
        )
        merge(existing, record, review_reason=reason)
    return out
