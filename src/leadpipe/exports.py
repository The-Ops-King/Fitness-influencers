"""Channel-agnostic CSV exports.

Three views off the same table. The CRM destination is undecided, so nothing
here is shaped to a specific vendor - these are plain CSVs any CRM can ingest.
"""

from __future__ import annotations

import csv
import hashlib
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .normalize import normalize_email, normalize_phone, strip_accents

log = logging.getLogger(__name__)

COLD_EMAIL_COLUMNS = [
    "email", "first_name", "full_name", "business_name", "niche",
    "booking_platform", "booking_url", "website", "instagram_handle",
    "personalization_detail", "qualification_score", "source_modules",
]

# The primary export: Instagram DM is the main outreach channel, so this
# carries everything needed to open a conversation without a second lookup.
INSTAGRAM_DM_COLUMNS = [
    "instagram_handle", "profile_url", "first_name", "full_name", "business_name",
    "niche", "followers", "booking_url", "booking_platform", "website",
    "running_meta_ads", "ad_days_running", "personalization_detail",
    "qualification_score", "source_modules",
]

# Meta's expected custom-audience column names.
META_AUDIENCE_COLUMNS = ["email", "phone", "fn", "ln", "country"]


@dataclass
class ExportResult:
    path: Path
    rows: int


def _first_name(full_name: str | None) -> str:
    if not full_name:
        return ""
    parts = [p for p in strip_accents(full_name).split() if p]
    return parts[0] if parts else ""


def _last_name(full_name: str | None) -> str:
    parts = [p for p in strip_accents(full_name or "").split() if p]
    return parts[-1] if len(parts) > 1 else ""


def personalization_detail(row: dict[str, Any]) -> str:
    """One concrete, scraped detail worth referencing in the first line.

    Ordered by how specific the observation is - an ad running for months is a
    sharper opener than a follower count.
    """
    days = row.get("ad_days_running")
    if row.get("running_meta_ads") and days:
        return f"running Meta ads for {int(days)} days"

    slot = row.get("booking_slot_minutes")
    platform = row.get("booking_platform")
    if slot and platform:
        return f"{int(slot)}-minute call on {platform}"

    followers = row.get("instagram_followers")
    if followers:
        return f"{int(followers):,} Instagram followers"

    if row.get("niche"):
        return f"{row['niche']} coaching"

    if row.get("youtube_channel"):
        return "posts client results on YouTube"

    return ""


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _write(path: Path, columns: list[str], rows: Iterable[dict[str, Any]]) -> ExportResult:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
            count += 1
    log.info("wrote %d rows to %s", count, path)
    return ExportResult(path=path, rows=count)


def export_cold_email(rows: Iterable[dict[str, Any]], path: Path, min_score: int = 50) -> ExportResult:
    """Verified emails only, score 50+, with merge fields for personalization."""
    held_back = 0

    def build() -> Iterable[dict[str, Any]]:
        nonlocal held_back
        for row in rows:
            email = normalize_email(row.get("primary_email"))
            # Never export an address that has not cleared verification.
            if email and row.get("email_verify_status") != "deliverable":
                held_back += 1
            if not email or row.get("email_verify_status") != "deliverable":
                continue
            if (row.get("qualification_score") or 0) < min_score:
                continue
            if row.get("status") == "rejected":
                continue
            yield {
                "email": email,
                "first_name": _first_name(row.get("full_name")),
                "full_name": row.get("full_name") or "",
                "business_name": row.get("business_name") or "",
                "niche": row.get("niche") or "",
                "booking_platform": row.get("booking_platform") or "",
                "booking_url": row.get("booking_url") or "",
                "website": row.get("website") or "",
                "instagram_handle": row.get("instagram_handle") or "",
                "personalization_detail": personalization_detail(row),
                "qualification_score": row.get("qualification_score") or 0,
                "source_modules": "|".join(row.get("source_modules") or []),
            }

    result = _write(path, COLD_EMAIL_COLUMNS, build())
    if held_back:
        # Almost always means EMAIL_VERIFIER is unset, so every address is
        # stuck at "unknown". Say so rather than shipping an empty CSV quietly.
        log.warning(
            "%d records held back from %s: email present but not verified deliverable. "
            "Set EMAIL_VERIFIER and EMAIL_VERIFIER_KEY, then run `leadpipe enrich`.",
            held_back,
            path.name,
        )
    return result


def export_instagram_dm(rows: Iterable[dict[str, Any]], path: Path, min_score: int = 40) -> ExportResult:
    """The primary outreach list. Handles only - DM needs no verified email.

    Sorted by score descending: DM is worked by hand, so the order the rows come
    out in is the order they get worked.
    """
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()

    for row in rows:
        handle = (row.get("instagram_handle") or "").strip().lstrip("@").lower()
        if not handle or handle in seen:
            continue
        if (row.get("qualification_score") or 0) < min_score:
            continue
        if row.get("status") == "rejected":
            continue
        seen.add(handle)
        selected.append(
            {
                "instagram_handle": handle,
                "profile_url": f"https://instagram.com/{handle}",
                "first_name": _first_name(row.get("full_name")),
                "full_name": row.get("full_name") or "",
                "business_name": row.get("business_name") or "",
                "niche": row.get("niche") or "",
                "followers": row.get("instagram_followers") or "",
                "booking_url": row.get("booking_url") or "",
                "booking_platform": row.get("booking_platform") or "",
                "website": row.get("website") or "",
                "running_meta_ads": "yes" if row.get("running_meta_ads") else "",
                "ad_days_running": row.get("ad_days_running") or "",
                "personalization_detail": personalization_detail(row),
                "qualification_score": row.get("qualification_score") or 0,
                "source_modules": "|".join(row.get("source_modules") or []),
            }
        )

    selected.sort(key=lambda r: r["qualification_score"], reverse=True)
    return _write(path, INSTAGRAM_DM_COLUMNS, selected)


def export_meta_audience(
    rows: Iterable[dict[str, Any]],
    path: Path,
    min_score: int = 70,
    hash_values: bool = True,
) -> ExportResult:
    """Highest-confidence records only, in Meta's column format.

    Deliberately a narrower cut than the email export: a noisy seed list
    produces a fuzzier lookalike, so the default floor is well above the
    outreach floor.
    """

    def build() -> Iterable[dict[str, Any]]:
        for row in rows:
            if (row.get("qualification_score") or 0) < min_score:
                continue
            if row.get("status") == "rejected":
                continue
            email = normalize_email(row.get("primary_email"))
            phone = normalize_phone(row.get("phone"))
            if not email and not phone:
                continue
            # Meta expects lowercase, trimmed values; digits only for phone.
            email_value = email or ""
            phone_value = re.sub(r"\D", "", phone or "")
            first = _first_name(row.get("full_name")).lower()
            last = _last_name(row.get("full_name")).lower()
            country = (row.get("location_country") or "").lower()[:2]

            if hash_values:
                email_value = _sha256(email_value) if email_value else ""
                phone_value = _sha256(phone_value) if phone_value else ""
                first = _sha256(first) if first else ""
                last = _sha256(last) if last else ""

            yield {
                "email": email_value,
                "phone": phone_value,
                "fn": first,
                "ln": last,
                "country": country,
            }

    return _write(path, META_AUDIENCE_COLUMNS, build())
