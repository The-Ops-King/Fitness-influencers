"""SERP client, paid and free.

Scraping Google directly gets blocked and produces garbage at this volume, so
this goes through a provider. Two are free:

  brave       free tier, recurring monthly allowance, needs a key
  duckduckgo  no key at all, best effort - see DuckDuckGoProvider

and three are paid: serper, serpapi, scraperapi. The provider is swappable
because vendors change pricing constantly, and because starting free and
upgrading later should not require touching any module.

Every provider shares one process-wide call budget so a run cannot blow through
a free monthly allowance (or a paid balance) in a single sweep.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit

from .config import get_config
from .http import HttpClient

log = logging.getLogger(__name__)

FREE_PROVIDERS = {"brave", "duckduckgo"}
# Providers that need no credential at all.
KEYLESS_PROVIDERS = {"duckduckgo"}
# Preference order for SERP_PROVIDER=auto: best result quality first, ending on
# the one that always works. Something usable runs with zero setup, and adding a
# key silently upgrades every module.
AUTO_ORDER = ("serper", "serpapi", "scraperapi", "brave", "duckduckgo")


class SerpBudget:
    """Process-wide cap on SERP calls.

    Shared across modules 1, 5 and 6, because on a free tier the allowance is
    monthly and a single `run-all` would otherwise consume all of it.
    """

    def __init__(self, limit: int | None = None) -> None:
        self._lock = threading.Lock()
        self._limit = limit
        self._used = 0
        self._warned = False

    def configure(self, limit: int | None) -> None:
        with self._lock:
            self._limit = limit

    def reset(self) -> None:
        with self._lock:
            self._used = 0
            self._warned = False

    @property
    def used(self) -> int:
        return self._used

    @property
    def remaining(self) -> int | None:
        if self._limit is None:
            return None
        return max(0, self._limit - self._used)

    def take(self) -> bool:
        """Claim one call. False once the budget is spent."""
        with self._lock:
            if self._limit is not None and self._used >= self._limit:
                if not self._warned:
                    log.warning(
                        "SERP budget of %d calls exhausted; remaining queries will be skipped. "
                        "Raise SERP_MAX_CALLS_PER_RUN to continue.",
                        self._limit,
                    )
                    self._warned = True
                return False
            self._used += 1
            return True


#: Shared by every SerpClient unless one is passed explicitly.
BUDGET = SerpBudget()


@dataclass
class SerpHit:
    title: str
    link: str
    snippet: str
    position: int
    query: str


class SerpClient:
    def __init__(self, client: HttpClient | None = None, budget: SerpBudget | None = None) -> None:
        self.config = get_config()
        self.provider = self._resolve_provider(self.config.serp_provider)
        # SERP endpoints are APIs, not crawls; and they get their own proxy pool.
        self.http = client or HttpClient("serp", respect_robots=False)
        # An explicitly supplied budget keeps its own limit; only the shared
        # process-wide one is (re)configured from the environment.
        if budget is None:
            self.budget = BUDGET
            self.budget.configure(self.config.serp_max_calls)
        else:
            self.budget = budget

    def _resolve_provider(self, configured: str) -> str:
        """Resolve "auto" to the best provider that actually has a credential."""
        if configured != "auto":
            return configured
        for candidate in AUTO_ORDER:
            if candidate in KEYLESS_PROVIDERS or self._key_for(candidate):
                if candidate in KEYLESS_PROVIDERS:
                    log.info(
                        "SERP_PROVIDER=auto resolved to %s (no API key found). "
                        "Set BRAVE_API_KEY or SERPER_API_KEY for better results.",
                        candidate,
                    )
                else:
                    log.info("SERP_PROVIDER=auto resolved to %s", candidate)
                return candidate
        return "duckduckgo"

    def available(self) -> bool:
        """True when this provider can run. Keyless providers are always ready."""
        if self.provider in KEYLESS_PROVIDERS:
            return True
        return bool(self._key())

    @property
    def is_free(self) -> bool:
        return self.provider in FREE_PROVIDERS

    def _key(self) -> str | None:
        return self._key_for(self.provider)

    def _key_for(self, provider: str) -> str | None:
        return {
            "serper": self.config.serper_api_key,
            "serpapi": self.config.serpapi_key,
            "scraperapi": self.config.scraperapi_key,
            "brave": self.config.brave_api_key,
        }.get(provider)

    def search(self, query: str, *, pages: int = 10, per_page: int = 10, country: str = "us") -> list[SerpHit]:
        """Run one query and paginate deep. 10+ pages per query is the intent."""
        key = self._key()
        if not key and self.provider not in KEYLESS_PROVIDERS:
            log.warning("no API key for SERP provider %r; skipping query %r", self.provider, query)
            return []

        hits: list[SerpHit] = []
        seen_links: set[str] = set()

        for page in range(1, pages + 1):
            if not self.budget.take():
                break
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

    def _search_page(self, key: str | None, query: str, page: int, per_page: int, country: str) -> list[SerpHit]:
        if self.provider == "brave":
            return self._brave(key, query, page, per_page, country)

        if self.provider == "duckduckgo":
            return self._duckduckgo(query, page)

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

    # -- free providers ----------------------------------------------------

    def _brave(self, key: str | None, query: str, page: int, per_page: int, country: str) -> list[SerpHit]:
        """Brave Search API. Free tier is rate limited to roughly 1 query/sec.

        Brave paginates by result offset in pages of `count`, and caps the
        offset, so deep pagination stops earlier than on a paid Google API.
        """
        count = min(per_page, 20)
        offset = page - 1
        if offset > 9:  # Brave rejects offsets beyond this
            return []
        response = self.http.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"X-Subscription-Token": key or "", "Accept": "application/json"},
            params={"q": query, "count": count, "offset": offset, "country": country},
        )
        if not response.ok:
            log.warning("brave search failed for %r: %s", query, response.error or response.status_code)
            return []
        results = (response.json().get("web") or {}).get("results", [])
        return self._parse(results, query, "title", "url", "description")

    def _duckduckgo(self, query: str, page: int) -> list[SerpHit]:
        """DuckDuckGo's keyless HTML endpoint. Free, and best effort.

        No API key and no cost, but DDG rate limits aggressively and returns an
        empty page rather than an error when it throttles. Treat a short result
        set as throttling, not as an exhausted query.
        """
        response = self.http.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query, "s": str((page - 1) * 30), "dc": str((page - 1) * 30)},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if not response.ok:
            log.warning("duckduckgo failed for %r: %s", query, response.error or response.status_code)
            return []

        from .enrich.html import soup_of

        soup = soup_of(response.text)
        hits: list[SerpHit] = []
        for index, anchor in enumerate(soup.select("a.result__a")):
            href = anchor.get("href")
            link = _unwrap_ddg(href)
            if not link:
                continue
            container = anchor.find_parent(class_=re.compile("result"))
            snippet_el = container.select_one(".result__snippet") if container else None
            hits.append(
                SerpHit(
                    title=anchor.get_text(" ", strip=True),
                    link=link,
                    snippet=snippet_el.get_text(" ", strip=True) if snippet_el else "",
                    position=index + 1,
                    query=query,
                )
            )
        if not hits:
            log.info("duckduckgo returned nothing for %r (likely throttled)", query)
        return hits

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


def _unwrap_ddg(href: str | None) -> str | None:
    """DuckDuckGo wraps results as //duckduckgo.com/l/?uddg=<encoded target>."""
    if not href:
        return None
    if href.startswith("//"):
        href = "https:" + href
    parts = urlsplit(href)
    if "duckduckgo.com" in parts.netloc and parts.path.startswith("/l/"):
        target = parse_qs(parts.query).get("uddg", [])
        return unquote(target[0]) if target else None
    if parts.scheme in {"http", "https"}:
        return href
    return None
