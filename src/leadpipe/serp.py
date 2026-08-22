"""Paid SERP API client.

Scraping Google directly gets blocked and produces garbage at this volume, so
this goes through Serper / SerpAPI / ScraperAPI. Provider is swappable because
these vendors change pricing constantly.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from .config import get_config
from .http import HttpClient

log = logging.getLogger(__name__)


@dataclass
class SerpHit:
    title: str
    link: str
    snippet: str
    position: int
    query: str


class SerpClient:
    def __init__(self, client: HttpClient | None = None) -> None:
        self.config = get_config()
        self.provider = self.config.serp_provider
        # SERP endpoints are APIs, not crawls; and they get their own proxy pool.
        self.http = client or HttpClient("serp", respect_robots=False)

    def available(self) -> bool:
        return bool(self._key())

    def _key(self) -> str | None:
        return {
            "serper": self.config.serper_api_key,
            "serpapi": self.config.serpapi_key,
            "scraperapi": self.config.scraperapi_key,
        }.get(self.provider)

    def search(self, query: str, *, pages: int = 10, per_page: int = 10, country: str = "us") -> list[SerpHit]:
        """Run one query and paginate deep. 10+ pages per query is the intent."""
        key = self._key()
        if not key:
            log.warning("no API key for SERP provider %r; skipping query %r", self.provider, query)
            return []

        hits: list[SerpHit] = []
        seen_links: set[str] = set()

        for page in range(1, pages + 1):
            batch = self._search_page(key, query, page, per_page, country)
            if not batch:
                # An empty page means the result set is exhausted; stop paying.
                break
            fresh = 0
            for hit in batch:
                if hit.link in seen_links:
                    continue
                seen_links.add(hit.link)
                hits.append(hit)
                fresh += 1
            if fresh == 0:
                break
        return hits

    def _search_page(self, key: str, query: str, page: int, per_page: int, country: str) -> list[SerpHit]:
        if self.provider == "serper":
            response = self.http.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": key, "Content-Type": "application/json"},
                data=json.dumps({"q": query, "page": page, "num": per_page, "gl": country}),
            )
            payload = response.json()
            return self._parse(payload.get("organic", []), query, "title", "link", "snippet")

        if self.provider == "serpapi":
            response = self.http.get(
                "https://serpapi.com/search.json",
                params={
                    "q": query,
                    "api_key": key,
                    "start": (page - 1) * per_page,
                    "num": per_page,
                    "gl": country,
                    "engine": "google",
                },
            )
            payload = response.json()
            return self._parse(payload.get("organic_results", []), query, "title", "link", "snippet")

        if self.provider == "scraperapi":
            response = self.http.get(
                "https://api.scraperapi.com/structured/google/search",
                params={
                    "api_key": key,
                    "query": query,
                    "num": per_page,
                    "page": page,
                    "country_code": country,
                },
            )
            payload = response.json()
            return self._parse(payload.get("organic_results", []), query, "title", "link", "snippet")

        log.error("unknown SERP provider %r", self.provider)
        return []

    @staticmethod
    def _parse(items: list[dict], query: str, title_key: str, link_key: str, snippet_key: str) -> list[SerpHit]:
        hits: list[SerpHit] = []
        for index, item in enumerate(items):
            link = item.get(link_key)
            if not link:
                continue
            hits.append(
                SerpHit(
                    title=item.get(title_key, "") or "",
                    link=link,
                    snippet=item.get(snippet_key, "") or "",
                    position=int(item.get("position", index + 1) or index + 1),
                    query=query,
                )
            )
        return hits
