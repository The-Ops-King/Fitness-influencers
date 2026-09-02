"""Environment-driven configuration.

Every credential and tuning knob is read here so the modules stay free of
os.environ lookups. Missing credentials are not fatal at import time: a module
checks for its own key when it runs, so a missing YouTube key never blocks the
booking-link module.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

try:  # optional: .env is a convenience, not a requirement
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - exercised only in bare environments
    pass


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _optional_int(name: str, default: int | None) -> int | None:
    """Like _int, but an explicit empty value or 0 means "no limit"."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return None if value <= 0 else value


def _list(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [p.strip() for p in raw.split(",") if p.strip()]


@dataclass(frozen=True)
class ModuleLimits:
    """Per-module rate limit and proxy pool.

    Proxy pools are deliberately separate. Burning a residential IP on Instagram
    must not take the SERP module down with it.
    """

    requests_per_second: float
    max_concurrency: int
    proxies: list[str] = field(default_factory=list)
    timeout_seconds: int = 30
    max_retries: int = 3


@dataclass(frozen=True)
class Config:
    database_url: str

    # SERP
    serper_api_key: str | None
    serpapi_key: str | None
    scraperapi_key: str | None
    brave_api_key: str | None
    serp_provider: str  # auto | brave | duckduckgo | serper | serpapi | scraperapi
    #: Hard cap on SERP calls per run, shared across modules 1, 5 and 6.
    serp_max_calls: int | None

    # Meta
    meta_access_token: str | None
    meta_api_version: str

    # Instagram (via Apify actors - do not roll raw requests at this)
    apify_token: str | None
    apify_instagram_profile_actor: str
    apify_instagram_hashtag_actor: str

    # YouTube
    youtube_api_key: str | None

    # Email verification
    email_verifier: str  # zerobounce | neverbounce | millionverifier | none
    email_verifier_key: str | None

    # Targeting
    target_countries: list[str]
    min_followers: int
    max_followers: int
    outreach_score_floor: int
    nurture_score_floor: int

    limits: dict[str, ModuleLimits]

    @classmethod
    def load(cls) -> Config:
        return cls(
            database_url=os.getenv("DATABASE_URL", ""),
            serper_api_key=os.getenv("SERPER_API_KEY"),
            serpapi_key=os.getenv("SERPAPI_KEY"),
            scraperapi_key=os.getenv("SCRAPERAPI_KEY"),
            brave_api_key=os.getenv("BRAVE_API_KEY"),
            serp_provider=os.getenv("SERP_PROVIDER", "auto").lower(),
            serp_max_calls=_optional_int("SERP_MAX_CALLS_PER_RUN", 2000),
            meta_access_token=os.getenv("META_ACCESS_TOKEN"),
            meta_api_version=os.getenv("META_API_VERSION", "v21.0"),
            apify_token=os.getenv("APIFY_TOKEN"),
            apify_instagram_profile_actor=os.getenv(
                "APIFY_IG_PROFILE_ACTOR", "apify~instagram-profile-scraper"
            ),
            apify_instagram_hashtag_actor=os.getenv(
                "APIFY_IG_HASHTAG_ACTOR", "apify~instagram-hashtag-scraper"
            ),
            youtube_api_key=os.getenv("YOUTUBE_API_KEY"),
            email_verifier=os.getenv("EMAIL_VERIFIER", "none").lower(),
            email_verifier_key=os.getenv("EMAIL_VERIFIER_KEY"),
            target_countries=_list("TARGET_COUNTRIES") or ["US", "CA", "GB", "AU"],
            min_followers=_int("MIN_FOLLOWERS", 1000),
            max_followers=_int("MAX_FOLLOWERS", 150000),
            outreach_score_floor=_int("OUTREACH_SCORE_FLOOR", 50),
            nurture_score_floor=_int("NURTURE_SCORE_FLOOR", 30),
            limits={
                "serp": ModuleLimits(
                    requests_per_second=_float("SERP_RPS", 1.0),
                    max_concurrency=_int("SERP_CONCURRENCY", 4),
                    proxies=_list("SERP_PROXIES"),
                ),
                "web": ModuleLimits(
                    requests_per_second=_float("WEB_RPS", 3.0),
                    max_concurrency=_int("WEB_CONCURRENCY", 6),
                    proxies=_list("WEB_PROXIES"),
                ),
                "meta": ModuleLimits(
                    requests_per_second=_float("META_RPS", 1.0),
                    max_concurrency=_int("META_CONCURRENCY", 2),
                    proxies=_list("META_PROXIES"),
                ),
                "instagram": ModuleLimits(
                    requests_per_second=_float("IG_RPS", 0.3),
                    max_concurrency=_int("IG_CONCURRENCY", 1),
                    # Residential pool. Never shared with SERP.
                    proxies=_list("IG_PROXIES"),
                    timeout_seconds=_int("IG_TIMEOUT", 60),
                    max_retries=_int("IG_RETRIES", 2),
                ),
                "youtube": ModuleLimits(
                    requests_per_second=_float("YT_RPS", 2.0),
                    max_concurrency=_int("YT_CONCURRENCY", 3),
                    proxies=_list("YT_PROXIES"),
                ),
            },
        )

    def limits_for(self, pool: str) -> ModuleLimits:
        return self.limits.get(pool, self.limits["web"])


_cached: Config | None = None


def get_config(refresh: bool = False) -> Config:
    global _cached
    if _cached is None or refresh:
        _cached = Config.load()
    return _cached
