"""Module 1 - booking link fingerprint.

The highest-signal source in the pipeline. A public booking page tied to fitness
keywords is the closest observable proxy for "this person takes their own sales
calls", which is the one ICP criterion no database carries.

Call duration is captured here because it separates low-ticket 15-minute chats
from real 30-60 minute sales calls.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from ..enrich.booking import inspect_booking_page
from ..http import HttpClient
from ..keywords import booking_link_queries
from ..models import RawRecord
from ..normalize import detect_booking_platform, is_booking_url, normalize_url
from ..serp import SerpClient
from .base import ModuleContext, SourceModule, register

log = logging.getLogger(__name__)


class BookingSerpModule(SourceModule):
    name = "m1_booking_serp"

    def run(self, ctx: ModuleContext) -> Iterator[RawRecord]:
        serp = SerpClient()
        if not serp.available():
            log.warning("no SERP API key configured; module 1 cannot run")
            return

        web = HttpClient("web")
        queries: list[str] = ctx.options.get("queries") or booking_link_queries()
        pages = int(ctx.options.get("pages", 10))
        seen_booking: set[str] = set()

        for query in queries:
            for country in _serp_countries(ctx):
                for hit in serp.search(query, pages=pages, country=country):
                    booking_url = normalize_url(hit.link, strip_query=True)
                    if not booking_url or not is_booking_url(booking_url):
                        continue
                    if booking_url in seen_booking:
                        continue
                    seen_booking.add(booking_url)

                    facts = inspect_booking_page(booking_url, client=web)
                    # A dead booking link is not a lead. Skip before it costs
                    # enrichment spend downstream.
                    if not facts.reachable:
                        continue

                    yield RawRecord(
                        source_module=self.name,
                        source_url=booking_url,
                        payload={
                            "booking_url": booking_url,
                            "booking_platform": facts.platform or detect_booking_platform(booking_url),
                            "booking_slot_minutes": facts.slot_minutes,
                            "full_name": facts.owner_name,
                            "business_name": facts.business_name,
                            "instagram_handle": facts.instagram_handle,
                            "emails": facts.emails,
                            "outbound_links": facts.outbound_links[:40],
                            "evidence_text": " ".join(
                                p for p in (hit.title, hit.snippet, facts.text[:3000]) if p
                            ),
                            "serp_query": query,
                            "serp_position": hit.position,
                            "location_country": country.upper(),
                        },
                    )


def _serp_countries(ctx: ModuleContext) -> list[str]:
    override = ctx.options.get("countries")
    if override:
        return [c.lower() for c in override]
    # gl codes, not ISO country codes.
    mapping = {"US": "us", "CA": "ca", "GB": "uk", "UK": "uk", "AU": "au"}
    return [mapping.get(c.upper(), c.lower()) for c in ctx.config.target_countries]


register(BookingSerpModule)
