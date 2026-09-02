"""Module 2 - Meta Ad Library.

Self-qualifying: paying to book calls means they take calls and have budget.

Ad longevity is the best revenue proxy in the whole pipeline. Nobody keeps
paying for an ad that does not convert, so `ad_days_running` is weighted
heavily. The landing page is followed and re-run through booking detection.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime

from ..enrich.booking import inspect_booking_page, resolve_link_in_bio
from ..http import HttpClient
from ..keywords import META_AD_SEARCH_TERMS
from ..models import RawRecord
from ..normalize import (
    is_booking_url,
    is_link_in_bio,
    normalize_instagram_handle,
    normalize_url,
)
from .base import ModuleContext, SourceModule, register

log = logging.getLogger(__name__)

AD_FIELDS = ",".join(
    [
        "id",
        "page_id",
        "page_name",
        "ad_creative_bodies",
        "ad_creative_link_captions",
        "ad_creative_link_descriptions",
        "ad_creative_link_titles",
        "ad_delivery_start_time",
        "ad_delivery_stop_time",
        "ad_snapshot_url",
        "publisher_platforms",
    ]
)


@dataclass
class LandingFacts:
    """What following an ad's landing page turned up."""

    booking_url: str | None = None
    slot_minutes: int | None = None
    instagram_handle: str | None = None
    text: str = ""


def _first_handle(urls: list[str]) -> str | None:
    for url in urls:
        handle = normalize_instagram_handle(url)
        if handle:
            return handle
    return None


class MetaAdsModule(SourceModule):
    name = "m2_meta_ads"
    # The official API needs a Meta developer app plus ID verification.
    requires = ("meta_access_token",)

    def run(self, ctx: ModuleContext) -> Iterator[RawRecord]:
        http = HttpClient("meta", respect_robots=False)
        web = HttpClient("web")
        version = ctx.config.meta_api_version
        endpoint = f"https://graph.facebook.com/{version}/ads_archive"
        countries = ctx.options.get("countries") or ctx.config.target_countries
        terms = ctx.options.get("terms") or META_AD_SEARCH_TERMS
        seen_pages: set[str] = set()

        for term in terms:
            params = {
                "access_token": ctx.config.meta_access_token,
                "search_terms": term,
                "ad_reached_countries": str(list(countries)).replace("'", '"'),
                "ad_active_status": "ACTIVE",
                "ad_type": "ALL",
                "limit": 100,
                "fields": AD_FIELDS,
            }
            url: str | None = endpoint

            while url:
                response = http.get(url, params=params if url == endpoint else None)
                if not response.ok:
                    log.warning("Meta Ad Library error for %r: %s", term, response.error or response.status_code)
                    break

                payload = response.json()
                for ad in payload.get("data", []):
                    page_name = ad.get("page_name")
                    page_id = ad.get("page_id")
                    key = str(page_id or page_name or "")
                    if not key or key in seen_pages:
                        continue
                    seen_pages.add(key)

                    start = _parse_date(ad.get("ad_delivery_start_time"))
                    days = (date.today() - start).days if start else None
                    ad_copy = " ".join(ad.get("ad_creative_bodies") or [])
                    titles = " ".join(ad.get("ad_creative_link_titles") or [])
                    captions = ad.get("ad_creative_link_captions") or []

                    landing = _first_landing_page(captions)
                    facts = self._follow_landing(landing, web)

                    yield RawRecord(
                        source_module=self.name,
                        source_url=ad.get("ad_snapshot_url"),
                        payload={
                            "business_name": page_name,
                            "facebook_page": f"https://facebook.com/{page_id}" if page_id else None,
                            "website": landing,
                            "booking_url": facts.booking_url,
                            "booking_slot_minutes": facts.slot_minutes,
                            "instagram_handle": facts.instagram_handle,
                            "running_meta_ads": True,
                            "ad_first_seen_date": start.isoformat() if start else None,
                            "ad_days_running": days,
                            "location_country": countries[0] if countries else None,
                            "evidence_text": " ".join(p for p in (ad_copy, titles, facts.text) if p),
                            "meta_ad_id": ad.get("id"),
                            "search_term": term,
                        },
                    )

                url = (payload.get("paging") or {}).get("next")
                params = None

    @staticmethod
    def _follow_landing(landing: str | None, web: HttpClient) -> LandingFacts:
        """Re-run booking detection on the ad's landing page.

        The Instagram handle is harvested here too. A coach paying for Meta ads
        almost always links their Instagram from the landing page, and that
        handle is the primary contact channel - dropping it would waste the
        page fetch this module has already paid for.
        """
        if not landing:
            return LandingFacts()

        if is_booking_url(landing):
            facts = inspect_booking_page(landing, client=web)
            return LandingFacts(
                booking_url=landing,
                slot_minutes=facts.slot_minutes,
                instagram_handle=facts.instagram_handle,
                text=facts.text[:3000],
            )

        if is_link_in_bio(landing):
            # A bio hub also exposes the Instagram link alongside the booking one.
            handle = _first_handle(resolve_link_in_bio(landing, client=web, keep_all=True))
            for candidate in resolve_link_in_bio(landing, client=web):
                facts = inspect_booking_page(candidate, client=web)
                if facts.reachable:
                    return LandingFacts(
                        booking_url=candidate,
                        slot_minutes=facts.slot_minutes,
                        instagram_handle=facts.instagram_handle or handle,
                        text=facts.text[:3000],
                    )
            return LandingFacts(instagram_handle=handle)

        # A normal landing page: look for the booking link on it.
        from ..enrich.site_scrape import scrape_site

        site = scrape_site(landing, client=web, max_pages=2)
        if site.booking_urls:
            facts = inspect_booking_page(site.booking_urls[0], client=web)
            return LandingFacts(
                booking_url=site.booking_urls[0],
                slot_minutes=facts.slot_minutes,
                instagram_handle=site.instagram_handle or facts.instagram_handle,
                text=site.text[:3000],
            )
        return LandingFacts(instagram_handle=site.instagram_handle, text=site.text[:3000])


def _first_landing_page(captions: list[str]) -> str | None:
    for caption in captions:
        normalized = normalize_url(caption)
        if normalized:
            return normalized
    return None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).date()
    except (ValueError, AttributeError):
        return None


register(MetaAdsModule)
