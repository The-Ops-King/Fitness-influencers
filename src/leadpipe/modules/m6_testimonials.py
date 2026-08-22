"""Module 6 (opportunistic) - competitor customer proof.

Testimonial, case study, and wall-of-love pages from sales tools, CRMs, and
course platforms that serve coaches. Everyone named on those pages is
pre-qualified as someone who already buys software.

Seed URLs are configurable because vendor page layouts churn; the extraction
here is deliberately generic.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator

from ..enrich.booking import inspect_booking_page
from ..enrich.html import all_links, soup_of, visible_text
from ..http import HttpClient
from ..keywords import TESTIMONIAL_SEED_QUERIES
from ..models import RawRecord
from ..normalize import (
    clean_person_name,
    normalize_instagram_handle,
    normalize_url,
    root_domain,
)
from ..serp import SerpClient
from .base import ModuleContext, SourceModule, register

log = logging.getLogger(__name__)

# "- Jess Ryan, Online Fitness Coach" / "Jess Ryan | Fat Loss Coach"
ATTRIBUTION_RE = re.compile(
    r"([A-Z][A-Za-z'.\-]+(?:\s+[A-Z][A-Za-z'.\-]+){0,2})\s*[,|–—-]\s*"
    r"([A-Za-z ]{0,30}(?:coach|trainer|nutritionist)[A-Za-z ]{0,20})",
)

VENDOR_HOSTS_TO_SKIP = {"twitter.com", "x.com", "facebook.com", "linkedin.com", "youtube.com", "tiktok.com"}


class TestimonialModule(SourceModule):
    name = "m6_testimonials"

    def run(self, ctx: ModuleContext) -> Iterator[RawRecord]:
        web = HttpClient("web")
        pages: list[str] = [normalize_url(u) for u in ctx.options.get("seed_urls", []) if normalize_url(u)]

        # Discover vendor proof pages when none were supplied.
        if not pages:
            serp = SerpClient()
            if not serp.available():
                log.warning("no SERP API key and no seed_urls; module 6 cannot run")
                return
            for query in ctx.options.get("queries") or TESTIMONIAL_SEED_QUERIES:
                for hit in serp.search(query, pages=int(ctx.options.get("pages", 3))):
                    url = normalize_url(hit.link)
                    if url and url not in pages:
                        pages.append(url)

        seen_names: set[str] = set()

        for page_url in pages:
            response = web.get(page_url)
            if not response.ok or not response.text:
                continue

            html = response.text
            text = visible_text(html, limit=60_000)
            vendor_domain = root_domain(page_url)

            # Outbound links on a proof page point at the customers themselves.
            customer_sites: dict[str, None] = {}
            for link in all_links(html, base_url=page_url):
                clean = normalize_url(link)
                if not clean:
                    continue
                domain = root_domain(clean)
                if not domain or domain == vendor_domain or domain in VENDOR_HOSTS_TO_SKIP:
                    continue
                customer_sites.setdefault(clean, None)

            handles = {
                h for h in (normalize_instagram_handle(u) for u in customer_sites) if h
            }

            for match in ATTRIBUTION_RE.finditer(self._quote_text(html) or text):
                name = clean_person_name(match.group(1))
                role = match.group(2).strip()
                if not name or name.lower() in seen_names:
                    continue
                seen_names.add(name.lower())

                site = self._best_site_for(name, customer_sites)
                booking_url = None
                slot_minutes = None
                if site:
                    from ..enrich.site_scrape import scrape_site

                    facts = scrape_site(site, client=web, max_pages=2)
                    if facts.booking_urls:
                        booking_url = facts.booking_urls[0]
                        booking = inspect_booking_page(booking_url, client=web)
                        slot_minutes = booking.slot_minutes

                yield RawRecord(
                    source_module=self.name,
                    source_url=page_url,
                    payload={
                        "full_name": name,
                        "business_name": role,
                        "website": site,
                        "booking_url": booking_url,
                        "booking_slot_minutes": slot_minutes,
                        "instagram_handle": next(iter(handles), None) if len(handles) == 1 else None,
                        "vendor_page": page_url,
                        "vendor_domain": vendor_domain,
                        "evidence_text": f"{name} {role}",
                    },
                )

    @staticmethod
    def _quote_text(html: str) -> str | None:
        """Prefer blockquote/testimonial markup over the whole page body."""
        soup = soup_of(html)
        blocks = soup.find_all(
            ["blockquote", "figcaption"]
        ) + soup.find_all(attrs={"class": re.compile(r"testimonial|quote|review", re.I)})
        if not blocks:
            return None
        return " ".join(b.get_text(" ", strip=True) for b in blocks)[:60_000]

    @staticmethod
    def _best_site_for(name: str, sites: dict[str, None]) -> str | None:
        """Match a person to their site by surname appearing in the domain."""
        tokens = [t.lower() for t in name.split() if len(t) > 2]
        for url in sites:
            domain = (root_domain(url) or "").lower()
            if any(token in domain for token in tokens):
                return url
        return None


register(TestimonialModule)
