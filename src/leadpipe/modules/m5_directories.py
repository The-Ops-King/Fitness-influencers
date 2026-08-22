"""Module 5 - platform and directory exhaust.

Coaching software profiles (Trainerize, TrueCoach, Everfit, PT Distinction),
paid Skool communities, freelance marketplaces, service-area Google Business
Profiles, and podcast show notes. Reached through the SERP API with site:
filters, then each profile page is scraped for a booking link and socials.

An owner of a $50-200/mo Skool community is selling on calls almost by
definition, so those are kept even when the profile page is thin.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from urllib.parse import urlsplit

from ..enrich.booking import inspect_booking_page, resolve_link_in_bio
from ..enrich.html import all_links, page_title, visible_text
from ..http import HttpClient
from ..keywords import DIRECTORY_SITES, GBP_QUERIES, PODCAST_QUERIES, directory_queries
from ..models import RawRecord
from ..normalize import (
    clean_person_name,
    extract_emails,
    is_booking_url,
    is_link_in_bio,
    normalize_instagram_handle,
    normalize_url,
)
from ..serp import SerpClient
from .base import ModuleContext, SourceModule, register

log = logging.getLogger(__name__)

_PLATFORM_BY_HOST = dict(DIRECTORY_SITES)


class DirectoryModule(SourceModule):
    name = "m5_directories"

    def run(self, ctx: ModuleContext) -> Iterator[RawRecord]:
        serp = SerpClient()
        if not serp.available():
            log.warning("no SERP API key configured; module 5 cannot run")
            return

        web = HttpClient("web")
        queries = ctx.options.get("queries") or (directory_queries() + GBP_QUERIES + PODCAST_QUERIES)
        pages = int(ctx.options.get("pages", 5))
        seen: set[str] = set()

        for query in queries:
            for hit in serp.search(query, pages=pages):
                url = normalize_url(hit.link)
                if not url or url in seen:
                    continue
                seen.add(url)

                record = self._scrape_profile(url, hit.title, hit.snippet, query, web)
                if record:
                    yield record

    def _scrape_profile(
        self, url: str, title: str, snippet: str, query: str, web: HttpClient
    ) -> RawRecord | None:
        response = web.get(url)
        if not response.ok or not response.text:
            # Some directories block crawls. The SERP snippet alone is still a
            # weak lead, but not one worth paying to enrich.
            return None

        html = response.text
        text = visible_text(html, limit=20_000)
        links = [normalize_url(u) or u for u in all_links(html, base_url=url)]

        booking_url = next((u for u in links if is_booking_url(u)), None)
        slot_minutes = None
        if booking_url is None:
            for hub in (u for u in links if is_link_in_bio(u)):
                candidates = resolve_link_in_bio(hub, client=web)
                if candidates:
                    booking_url = candidates[0]
                    break
        if booking_url:
            facts = inspect_booking_page(booking_url, client=web)
            slot_minutes = facts.slot_minutes

        instagram = next((h for h in (normalize_instagram_handle(u) for u in links) if h), None)
        host = urlsplit(url).netloc
        platform = next(
            (p for h, p in _PLATFORM_BY_HOST.items() if host == h or host.endswith("." + h)), "web"
        )

        return RawRecord(
            source_module=self.name,
            source_url=url,
            payload={
                "full_name": clean_person_name(page_title(html) or title),
                "business_name": page_title(html) or title,
                "website": url,
                "booking_url": booking_url,
                "booking_slot_minutes": slot_minutes,
                "instagram_handle": instagram,
                "emails": extract_emails(text),
                "directory_platform": platform,
                "outbound_links": links[:40],
                "evidence_text": " ".join(p for p in (title, snippet, text[:4000]) if p),
                "serp_query": query,
            },
        )


register(DirectoryModule)
