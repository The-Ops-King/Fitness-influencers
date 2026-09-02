"""Module 3 - Instagram and link-in-bio.

Highest volume, highest engineering overhead, and the module most likely to
break. It runs through Apify's maintained actors on a residential proxy pool
rather than raw requests, and it is fully isolated: when Instagram blocks, this
module fails alone and the rest of the run finishes.

The key move is following the external link one hop deeper. Linktree, Stan
Store, Beacons and Koji are the middle layer, and the booking link almost always
sits behind them. Stan Store presence in particular skews to the target revenue
band.

Two modes:

  discovery (default)
      Hashtag seeding, bio search and follow-graph expansion. Tens of thousands
      of billed Apify results per run.

  enrichment only (enrich_only=true)
      Skips discovery entirely and fetches profiles for handles already in the
      database that have no follower count. One billed result per handle - two
      to three orders of magnitude cheaper. Handles themselves are harvested for
      free by modules 1, 2, 4, 5 and 6, so this is usually the only mode worth
      paying for.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from ..enrich.booking import inspect_booking_page, resolve_link_in_bio
from ..http import HttpClient
from ..keywords import INSTAGRAM_BIO_TERMS, INSTAGRAM_HASHTAGS
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

APIFY_BASE = "https://api.apify.com/v2"


class InstagramModule(SourceModule):
    name = "m3_instagram"
    requires = ("apify_token",)

    def __init__(self) -> None:
        self._results_used = 0
        self._budget: int | None = None

    def run(self, ctx: ModuleContext) -> Iterator[RawRecord]:
        # Its own pool. Never shared with SERP - a burnt residential IP here
        # must not take the search modules down.
        api = HttpClient("instagram", respect_robots=False)
        web = HttpClient("web")
        self._results_used = 0
        self._budget = ctx.config.apify_max_results
        enrich_only = bool(ctx.options.get("enrich_only"))

        if enrich_only:
            handles = self._handles_to_enrich(ctx)
            log.info("module 3 (enrich only): %d handles need follower data", len(handles))
        else:
            handles = self._seed_handles(ctx, api)
            log.info("module 3 seeded %d handles", len(handles))
            expand = int(ctx.options.get("expand_hops", 1))
            if expand:
                handles = self._expand_graph(ctx, api, handles, hops=expand)
            handles = sorted(handles)

        for profile in self._fetch_profiles(ctx, api, list(handles)):
            record = self._build_record(ctx, profile, web, enrich_only=enrich_only)
            if record:
                yield record

    def _handles_to_enrich(self, ctx: ModuleContext) -> list[str]:
        """Handles awaiting follower data, highest-scoring first.

        `handles` can be supplied directly, which is what makes a dry run (and a
        test) possible without touching the database.
        """
        supplied = ctx.options.get("handles")
        if supplied:
            return [h for h in (normalize_instagram_handle(x) for x in supplied) if h]
        if ctx.dry_run:
            log.warning("enrich_only dry run needs an explicit handles option; nothing to do")
            return []

        from .. import db

        # Cap the work list by whichever ceiling is tighter: the caller's limit,
        # or the run's Apify result budget.
        caps = [c for c in (ctx.limit, ctx.config.apify_max_results) if c]
        return db.handles_missing_followers(limit=min(caps) if caps else None)

    # -- seeding -----------------------------------------------------------

    def _seed_handles(self, ctx: ModuleContext, api: HttpClient) -> set[str]:
        seeds: set[str] = {
            h for h in (normalize_instagram_handle(s) for s in ctx.options.get("seed_handles", [])) if h
        }
        hashtags = ctx.options.get("hashtags") or INSTAGRAM_HASHTAGS
        per_tag = int(ctx.options.get("posts_per_hashtag", 200))

        for hashtag in hashtags:
            items = self._run_actor(
                ctx,
                api,
                ctx.config.apify_instagram_hashtag_actor,
                {"hashtags": [hashtag], "resultsLimit": per_tag, "searchType": "hashtag"},
            )
            for item in items:
                handle = normalize_instagram_handle(
                    item.get("ownerUsername") or item.get("username") or item.get("url")
                )
                if handle:
                    seeds.add(handle)

        # Bio keyword search rides the same actor via a search query.
        for term in ctx.options.get("bio_terms") or INSTAGRAM_BIO_TERMS:
            items = self._run_actor(
                ctx, api, ctx.config.apify_instagram_hashtag_actor,
                {"search": term, "searchType": "user", "resultsLimit": 100},
            )
            for item in items:
                handle = normalize_instagram_handle(item.get("username") or item.get("url"))
                if handle:
                    seeds.add(handle)

        return seeds

    def _expand_graph(self, ctx: ModuleContext, api: HttpClient, handles: set[str], hops: int) -> set[str]:
        """Mid-tier coaches follow each other heavily; the graph is dense here."""
        expanded = set(handles)
        frontier = list(handles)[: int(ctx.options.get("expand_seeds", 50))]

        for _ in range(hops):
            next_frontier: list[str] = []
            for handle in frontier:
                items = self._run_actor(
                    ctx,
                    api,
                    ctx.config.apify_instagram_profile_actor,
                    {
                        "usernames": [handle],
                        "resultsType": "following",
                        "resultsLimit": int(ctx.options.get("graph_limit", 200)),
                    },
                )
                for item in items:
                    neighbour = normalize_instagram_handle(item.get("username") or item.get("url"))
                    if neighbour and neighbour not in expanded:
                        expanded.add(neighbour)
                        next_frontier.append(neighbour)
            frontier = next_frontier
            if not frontier:
                break
        return expanded

    # -- profiles ----------------------------------------------------------

    def _fetch_profiles(self, ctx: ModuleContext, api: HttpClient, handles: list[str]) -> Iterator[dict]:
        batch = int(ctx.options.get("profile_batch", 50))
        for start in range(0, len(handles), batch):
            chunk = handles[start : start + batch]
            items = self._run_actor(
                ctx,
                api,
                ctx.config.apify_instagram_profile_actor,
                {"usernames": chunk, "resultsType": "details"},
            )
            yield from items

    def _build_record(
        self, ctx: ModuleContext, profile: dict, web: HttpClient, enrich_only: bool = False
    ) -> RawRecord | None:
        handle = normalize_instagram_handle(profile.get("username") or profile.get("url"))
        if not handle:
            return None

        followers = _int(profile.get("followersCount") or profile.get("followers"))
        # In discovery this band is a filter: below the floor cannot be at $5K/mo,
        # above the ceiling is a big name, and neither is worth a profile fetch.
        #
        # In enrichment the record already exists, so dropping it here would
        # leave a 400K-follower account sitting un-rejected in the database
        # forever. Yield it instead and let the >150K disqualifier do its job.
        in_band = followers is None or (
            ctx.config.min_followers <= followers <= ctx.config.max_followers
        )
        if not enrich_only and not in_band:
            return None

        bio = profile.get("biography") or ""
        external = normalize_url(profile.get("externalUrl") or profile.get("website"))
        # Professional accounts expose a contact email on the contact button.
        email = profile.get("businessEmail") or profile.get("publicEmail")

        booking_url: str | None = None
        slot_minutes: int | None = None
        hub_links: list[str] = []
        booking_text = ""

        # Enrichment is about follower counts. The record already has whatever
        # booking link the free modules found, and merge never overwrites it, so
        # re-walking bio hubs here would just be slow. Opt back in with
        # follow_links=true.
        follow_links = ctx.options.get("follow_links", not enrich_only)

        if external and follow_links:
            if is_booking_url(external):
                booking_url = external
            elif is_link_in_bio(external):
                # The one hop that turns a profile into a qualified record.
                hub_links = resolve_link_in_bio(external, client=web)
                booking_url = hub_links[0] if hub_links else None
            else:
                from ..enrich.site_scrape import scrape_site

                site = scrape_site(external, client=web, max_pages=2)
                booking_url = site.booking_urls[0] if site.booking_urls else None
                booking_text = site.text[:2000]

        if booking_url:
            facts = inspect_booking_page(booking_url, client=web)
            slot_minutes = facts.slot_minutes
            booking_text = booking_text or facts.text[:2000]

        emails = [e for e in [email] if e] or extract_emails(bio)

        return RawRecord(
            source_module=self.name,
            source_url=f"https://instagram.com/{handle}",
            payload={
                "instagram_handle": handle,
                "instagram_followers": followers,
                "full_name": profile.get("fullName") or profile.get("full_name"),
                "business_name": profile.get("businessCategoryName"),
                "website": external,
                "booking_url": booking_url,
                "booking_slot_minutes": slot_minutes,
                "emails": emails,
                "link_in_bio": external if is_link_in_bio(external) else None,
                "stan_store": bool(external and "stan.store" in external),
                "hub_links": hub_links[:20],
                "evidence_text": " ".join(p for p in (bio, booking_text) if p),
            },
        )

    # -- apify -------------------------------------------------------------

    def _run_actor(self, ctx: ModuleContext, api: HttpClient, actor: str, payload: dict) -> list[dict[str, Any]]:
        """Run an Apify actor synchronously and return its dataset items."""
        import json

        requested = int(payload.get("resultsLimit") or len(payload.get("usernames") or []) or 1)
        if self._budget is not None and self._results_used + requested > self._budget:
            log.warning(
                "Apify result budget of %d reached (%d used); skipping further calls. "
                "Raise APIFY_MAX_RESULTS_PER_RUN to continue.",
                self._budget,
                self._results_used,
            )
            return []

        url = f"{APIFY_BASE}/acts/{actor}/run-sync-get-dataset-items"
        response = api.post(
            url,
            params={"token": ctx.config.apify_token},
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=ctx.config.limits_for("instagram").timeout_seconds * 10,
        )
        if not response.ok:
            # Expected failure mode. Log and continue; do not raise.
            log.warning("apify actor %s failed: %s", actor, response.error or response.status_code)
            return []
        data = response.json()
        if isinstance(data, list):
            items = [d for d in data if isinstance(d, dict)]
            self._results_used += len(items)
            return items
        return []


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


register(InstagramModule)
