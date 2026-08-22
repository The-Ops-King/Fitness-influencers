"""The canonical record and its provenance wrapper."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, date, datetime
from typing import Any

STATUS_RAW = "raw"
STATUS_ENRICHED = "enriched"
STATUS_VERIFIED = "verified"
STATUS_REJECTED = "rejected"

# Field -> source URL that produced the current value.
Provenance = dict[str, str]


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class Coach:
    """One coach. Mirrors the `coaches` table."""

    id: int | None = None
    full_name: str | None = None
    business_name: str | None = None
    primary_email: str | None = None
    secondary_emails: list[str] = field(default_factory=list)
    phone: str | None = None
    instagram_handle: str | None = None
    instagram_followers: int | None = None
    website: str | None = None
    booking_url: str | None = None
    booking_platform: str | None = None
    booking_slot_minutes: int | None = None
    youtube_channel: str | None = None
    facebook_page: str | None = None
    linkedin_url: str | None = None
    running_meta_ads: bool = False
    ad_first_seen_date: date | None = None
    ad_days_running: int | None = None
    niche: str | None = None
    location_country: str | None = None
    location_city: str | None = None
    source_modules: list[str] = field(default_factory=list)
    first_seen_at: datetime = field(default_factory=utcnow)
    last_verified_at: datetime | None = None
    qualification_score: int = 0
    status: str = STATUS_RAW
    reject_reason: str | None = None

    # Derived / operational
    dedupe_email: str | None = None
    dedupe_booking_url: str | None = None
    dedupe_instagram: str | None = None
    dedupe_domain: str | None = None
    dedupe_name_key: str | None = None
    email_verify_status: str | None = None
    needs_manual_review: bool = False
    review_reason: str | None = None
    price_point_usd: float | None = None
    team_language: bool | None = None
    has_physical_address: bool | None = None

    # Not persisted on `coaches`; written out to field_provenance.
    provenance: Provenance = field(default_factory=dict)
    # Free-form text harvested for scoring/filters (bio, about page, ad copy).
    evidence_text: str = ""

    def to_row(self) -> dict[str, Any]:
        """Column dict for the `coaches` table (drops non-column attributes)."""
        skip = {"provenance", "evidence_text", "id"}
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name not in skip}

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RawRecord:
    """A single observation from one module, destined for `raw_records`."""

    source_module: str
    payload: dict[str, Any]
    source_url: str | None = None
    collected_at: datetime = field(default_factory=utcnow)

    def content_hash(self) -> str:
        """Stable hash over module + payload. Re-runs collapse onto the same row."""
        blob = json.dumps(
            {"m": self.source_module, "p": _stable(self.payload)},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _stable(value: Any) -> Any:
    """Drop volatile keys so an unchanged page does not produce a new hash weekly."""
    volatile = {"collected_at", "scraped_at", "timestamp", "fetched_at", "run_id", "cursor"}
    if isinstance(value, dict):
        return {k: _stable(v) for k, v in sorted(value.items()) if k not in volatile}
    if isinstance(value, list):
        return [_stable(v) for v in value]
    return value
