"""Module 4 - YouTube.

Client testimonial and transformation content is a reliable tell for a coach
with a real client roster. The 500-20,000 subscriber band is the sweet spot:
big enough to be working, small enough to still be the operator.

Booking links live in video descriptions and the channel About tab, so both are
parsed.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from typing import Any

from ..enrich.booking import inspect_booking_page, resolve_link_in_bio
from ..http import HttpClient
from ..keywords import YOUTUBE_QUERIES
from ..models import RawRecord
from ..normalize import (
    extract_emails,
    is_booking_url,
    is_link_in_bio,
    normalize_instagram_handle,
    normalize_url,
)
from .base import ModuleContext, SourceModule, register

log = logging.getLogger(__name__)

API = "https://www.googleapis.com/youtube/v3"
URL_RE = re.compile(r'https?://[^\s"\'<>)]{6,300}')

MIN_SUBS = 500
MAX_SUBS = 20_000


class YouTubeModule(SourceModule):
    name = "m4_youtube"
    requires = ("youtube_api_key",)

    def run(self, ctx: ModuleContext) -> Iterator[RawRecord]:
        api = HttpClient("youtube", respect_robots=False)
        web = HttpClient("web")
        key = ctx.config.youtube_api_key
        queries = ctx.options.get("queries") or YOUTUBE_QUERIES
        min_subs = int(ctx.options.get("min_subscribers", MIN_SUBS))
        max_subs = int(ctx.options.get("max_subscribers", MAX_SUBS))

        channel_ids: dict[str, str] = {}  # channel_id -> description text seen in search

        for query in queries:
            page_token: str | None = None
            for _ in range(int(ctx.options.get("pages", 5))):
                params: dict[str, Any] = {
                    "key": key,
                    "q": query,
                    "part": "snippet",
                    "type": "video",
                    "maxResults": 50,
                    "relevanceLanguage": "en",
                    "order": "relevance",
                }
                if page_token:
                    params["pageToken"] = page_token

                response = api.get(f"{API}/search", params=params)
                if not response.ok:
                    log.warning("youtube search failed for %r: %s", query, response.error or response.status_code)
                    break

                payload = response.json()
                for item in payload.get("items", []):
                    snippet = item.get("snippet", {})
                    channel_id = snippet.get("channelId")
                    if not channel_id:
                        continue
                    text = " ".join(
                        p for p in (snippet.get("title"), snippet.get("description")) if p
                    )
                    channel_ids.setdefault(channel_id, "")
                    channel_ids[channel_id] += " " + text

                page_token = payload.get("nextPageToken")
                if not page_token:
                    break

        log.info("module 4 found %d candidate channels", len(channel_ids))

        for channel in self._fetch_channels(api, key, list(channel_ids)):
            stats = channel.get("statistics", {})
            subs = _int(stats.get("subscriberCount"))
            if stats.get("hiddenSubscriberCount"):
                subs = None
            if subs is not None and not (min_subs <= subs <= max_subs):
                continue

            snippet = channel.get("snippet", {})
            branding = (channel.get("brandingSettings") or {}).get("channel", {})
            channel_id = channel.get("id")
            about_text = " ".join(
                p for p in (snippet.get("description"), branding.get("description"), channel_ids.get(channel_id, "")) if p
            )

            booking_url, slot_minutes = self._find_booking(about_text, web)

            yield RawRecord(
                source_module=self.name,
                source_url=f"https://youtube.com/channel/{channel_id}",
                payload={
                    "youtube_channel": f"https://youtube.com/channel/{channel_id}",
                    "business_name": snippet.get("title"),
                    "full_name": snippet.get("title"),
                    "booking_url": booking_url,
                    "booking_slot_minutes": slot_minutes,
                    "website": self._first_site(about_text),
                    "instagram_handle": self._first_instagram(about_text),
                    "emails": extract_emails(about_text),
                    "location_country": snippet.get("country"),
                    "youtube_subscribers": subs,
                    "evidence_text": about_text[:5000],
                },
            )

    def _fetch_channels(self, api: HttpClient, key: str | None, ids: list[str]) -> Iterator[dict]:
        for start in range(0, len(ids), 50):
            chunk = ids[start : start + 50]
            response = api.get(
                f"{API}/channels",
                params={
                    "key": key,
                    "id": ",".join(chunk),
                    "part": "snippet,statistics,brandingSettings",
                    "maxResults": 50,
                },
            )
            if not response.ok:
                log.warning("youtube channels lookup failed: %s", response.error or response.status_code)
                continue
            yield from response.json().get("items", [])

    @staticmethod
    def _find_booking(text: str, web: HttpClient) -> tuple[str | None, int | None]:
        for raw in URL_RE.findall(text or ""):
            url = normalize_url(raw)
            if not url:
                continue
            if is_booking_url(url):
                facts = inspect_booking_page(url, client=web)
                return url, facts.slot_minutes
            if is_link_in_bio(url):
                for candidate in resolve_link_in_bio(url, client=web):
                    facts = inspect_booking_page(candidate, client=web)
                    if facts.reachable:
                        return candidate, facts.slot_minutes
        return None, None

    @staticmethod
    def _first_site(text: str) -> str | None:
        for raw in URL_RE.findall(text or ""):
            url = normalize_url(raw)
            if url and not is_booking_url(url) and "instagram.com" not in url and "youtube.com" not in url:
                return url
        return None

    @staticmethod
    def _first_instagram(text: str) -> str | None:
        for raw in URL_RE.findall(text or ""):
            handle = normalize_instagram_handle(raw)
            if handle:
                return handle
        return None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


register(YouTubeModule)
