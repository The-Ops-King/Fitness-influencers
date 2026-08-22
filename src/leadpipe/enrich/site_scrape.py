"""Coach website scraping: team size, pricing, call language, contact details."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import urljoin

from ..filters import (
    extract_price_usd,
    has_call_language,
    has_physical_address,
    has_team_language,
    mentions_remote_delivery,
)
from ..http import HttpClient
from ..normalize import (
    extract_emails,
    extract_phones,
    is_booking_url,
    is_checkout_url,
    normalize_instagram_handle,
    normalize_url,
)
from .html import all_links, page_title, visible_text

log = logging.getLogger(__name__)

# Where team language and contact details actually live.
CANDIDATE_PATHS = ["", "/about", "/about-us", "/contact", "/coaching", "/work-with-me", "/apply", "/pricing"]


@dataclass
class SiteFacts:
    url: str
    reachable: bool = False
    title: str | None = None
    text: str = ""
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    booking_urls: list[str] = field(default_factory=list)
    checkout_urls: list[str] = field(default_factory=list)
    instagram_handle: str | None = None
    team_language: bool = False
    physical_address: bool = False
    call_language: bool = False
    remote_delivery: bool = False
    price_usd: float | None = None
    pages_fetched: list[str] = field(default_factory=list)


def scrape_site(url: str, client: HttpClient | None = None, max_pages: int = 4) -> SiteFacts:
    """Fetch a handful of high-value pages and summarize what they say.

    Deliberately shallow: this is a qualification read, not a full crawl.
    """
    http = client or HttpClient("web")
    base = normalize_url(url)
    facts = SiteFacts(url=base or url)
    if not base:
        return facts

    collected_text: list[str] = []
    fetched = 0

    for path in CANDIDATE_PATHS:
        if fetched >= max_pages:
            break
        target = urljoin(base + "/", path.lstrip("/")) if path else base
        response = http.get(target)
        if not response.ok or not response.text:
            continue

        fetched += 1
        facts.reachable = True
        facts.pages_fetched.append(target)
        html = response.text

        if facts.title is None:
            facts.title = page_title(html)

        text = visible_text(html, limit=25_000)
        collected_text.append(text)

        for email in extract_emails(text + " " + html):
            if email not in facts.emails:
                facts.emails.append(email)
        for phone in extract_phones(text):
            if phone not in facts.phones:
                facts.phones.append(phone)

        for link in all_links(html, base_url=target):
            clean = normalize_url(link)
            if not clean:
                continue
            if is_booking_url(clean) and clean not in facts.booking_urls:
                facts.booking_urls.append(clean)
            elif is_checkout_url(clean) and clean not in facts.checkout_urls:
                facts.checkout_urls.append(clean)
            if facts.instagram_handle is None:
                facts.instagram_handle = normalize_instagram_handle(clean)

    blob = "\n".join(collected_text)
    facts.text = blob
    facts.team_language = has_team_language(blob)
    facts.physical_address = has_physical_address(blob)
    facts.call_language = has_call_language(blob)
    facts.remote_delivery = mentions_remote_delivery(blob)
    facts.price_usd = extract_price_usd(blob)
    return facts
