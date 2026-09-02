"""Booking page inspection - the highest-signal enrichment in the pipeline.

A public booking link for a sales call is the strongest available proxy for
"this person takes their own calls". This module confirms the link resolves,
pulls the owner and business name off it, reads the slot duration, and collects
any social profiles the page exposes.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from ..http import HttpClient
from ..normalize import (
    clean_person_name,
    detect_booking_platform,
    extract_emails,
    is_booking_url,
    is_link_in_bio,
    normalize_instagram_handle,
    normalize_url,
    parse_slot_minutes,
)
from .html import all_links, meta_content, page_title, visible_text

log = logging.getLogger(__name__)

# "Book a 30 Minute Strategy Call with Jess Ryan"
_OWNER_PATTERNS = [
    re.compile(r"\bwith\s+([A-Z][A-Za-z'.\-]+(?:\s+[A-Z][A-Za-z'.\-]+){0,2})\b"),
    re.compile(r"^([A-Z][A-Za-z'.\-]+(?:\s+[A-Z][A-Za-z'.\-]+){0,2})\s*[-|–]\s", re.M),
]


@dataclass
class BookingPageFacts:
    url: str
    reachable: bool = False
    platform: str | None = None
    slot_minutes: int | None = None
    owner_name: str | None = None
    business_name: str | None = None
    instagram_handle: str | None = None
    emails: list[str] = field(default_factory=list)
    outbound_links: list[str] = field(default_factory=list)
    text: str = ""


def inspect_booking_page(url: str, client: HttpClient | None = None) -> BookingPageFacts:
    """Fetch a booking URL and pull the facts that feed scoring."""
    facts = BookingPageFacts(url=url, platform=detect_booking_platform(url))
    http = client or HttpClient("web")

    # Calendly and friends render client-side, but the server response still
    # carries the event name and duration in <title> and og: tags.
    response = http.get(url)
    if not response.ok or not response.text:
        log.debug("booking page unreachable: %s (%s)", url, response.error or response.status_code)
        return facts

    facts.reachable = True
    html = response.text
    title = page_title(html) or ""
    description = meta_content(html, "og:description", "description") or ""
    og_title = meta_content(html, "og:title") or ""
    text = visible_text(html, limit=20_000)
    facts.text = text

    header = " ".join(p for p in (title, og_title, description) if p)
    facts.slot_minutes = parse_slot_minutes(header) or parse_slot_minutes(text[:4000])

    for pattern in _OWNER_PATTERNS:
        match = pattern.search(header)
        if match:
            candidate = clean_person_name(match.group(1))
            if candidate:
                facts.owner_name = candidate
                break

    site_name = meta_content(html, "og:site_name")
    if site_name:
        facts.business_name = site_name.strip()
    elif title and not facts.owner_name:
        facts.business_name = re.split(r"[-|–]", title)[0].strip() or None

    facts.emails = extract_emails(text)
    links = all_links(html, base_url=url)
    facts.outbound_links = [normalize_url(u) or u for u in links if not u.startswith(("mailto:", "tel:"))]

    for link in links:
        handle = normalize_instagram_handle(link)
        if handle:
            facts.instagram_handle = handle
            break

    return facts


def resolve_link_in_bio(
    url: str, client: HttpClient | None = None, max_hops: int = 2, keep_all: bool = False
) -> list[str]:
    """Walk a Linktree / Stan Store / Beacons page and return the links behind it.

    The booking link almost always sits one hop behind the bio hub, so following
    it is what turns an Instagram profile into a qualified record.

    `keep_all=True` returns every outbound link instead of only booking ones,
    which is how the Instagram handle is recovered from a bio hub.
    """
    http = client or HttpClient("web")
    seen: set[str] = set()
    found: dict[str, None] = {}
    frontier = [url]

    for _ in range(max_hops):
        next_frontier: list[str] = []
        for current in frontier:
            normalized = normalize_url(current)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)

            response = http.get(normalized)
            if not response.ok or not response.text:
                continue

            html = response.text
            links = all_links(html, base_url=normalized)
            # Stan Store and Beacons hydrate from JSON, so scan raw markup too.
            links += re.findall(r'https?://[^\s"\'<>\\]{6,300}', html)

            for link in links:
                clean = normalize_url(link)
                if not clean or clean in seen:
                    continue
                if is_booking_url(clean):
                    found.setdefault(clean, None)
                elif is_link_in_bio(clean):
                    next_frontier.append(clean)
                elif keep_all:
                    found.setdefault(clean, None)
        frontier = next_frontier
        if not frontier:
            break

    return list(found)
