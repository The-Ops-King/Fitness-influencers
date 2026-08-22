"""HTTP client with per-pool rate limiting, proxy rotation, and robots.txt checks.

Each pool ("serp", "web", "instagram", ...) gets its own limiter and its own
proxy list. Instagram will get blocked; keeping its pool isolated means the
block does not leak into the SERP or Meta modules.
"""

from __future__ import annotations

import logging
import random
import threading
import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests

from .config import ModuleLimits, get_config

log = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class RateLimiter:
    """Token bucket. Thread-safe, one per pool."""

    def __init__(self, requests_per_second: float) -> None:
        self.interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self._lock = threading.Lock()
        self._next_at = 0.0

    def acquire(self) -> None:
        if self.interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            wait = self._next_at - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            # Jitter keeps a fleet of workers from marching in lockstep.
            self._next_at = now + self.interval * random.uniform(0.85, 1.25)


class RobotsCache:
    """robots.txt lookups, cached per host.

    Fail-open on a fetch error: an unreachable robots.txt is not a disallow.
    """

    def __init__(self) -> None:
        self._cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._lock = threading.Lock()

    def allowed(self, url: str, user_agent: str = "*") -> bool:
        parts = urlsplit(url)
        if not parts.netloc:
            return False
        host_key = f"{parts.scheme}://{parts.netloc}"
        with self._lock:
            if host_key not in self._cache:
                self._cache[host_key] = self._fetch(host_key)
            parser = self._cache[host_key]
        if parser is None:
            return True
        try:
            return parser.can_fetch(user_agent, url)
        except Exception:
            return True

    @staticmethod
    def _fetch(host_key: str) -> urllib.robotparser.RobotFileParser | None:
        parser = urllib.robotparser.RobotFileParser()
        try:
            response = requests.get(
                f"{host_key}/robots.txt", timeout=10, headers={"User-Agent": DEFAULT_UA}
            )
            if response.status_code >= 400:
                return None
            parser.parse(response.text.splitlines())
            return parser
        except requests.RequestException:
            return None


@dataclass
class Response:
    url: str
    status_code: int
    text: str
    ok: bool
    error: str | None = None

    def json(self) -> dict:
        import json

        try:
            return json.loads(self.text)
        except (ValueError, TypeError):
            return {}


_ROBOTS = RobotsCache()
_LIMITERS: dict[str, RateLimiter] = {}
_LIMITER_LOCK = threading.Lock()


def limiter_for(pool: str, limits: ModuleLimits) -> RateLimiter:
    with _LIMITER_LOCK:
        if pool not in _LIMITERS:
            _LIMITERS[pool] = RateLimiter(limits.requests_per_second)
        return _LIMITERS[pool]


class HttpClient:
    """Per-pool client. Construct one per module, not one globally."""

    def __init__(self, pool: str = "web", *, respect_robots: bool = True) -> None:
        config = get_config()
        self.pool = pool
        self.limits = config.limits_for(pool)
        self.respect_robots = respect_robots
        self.limiter = limiter_for(pool, self.limits)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": DEFAULT_UA, "Accept-Language": "en-US,en;q=0.9"})
        self._proxy_index = 0
        self._proxy_lock = threading.Lock()

    def _next_proxy(self) -> dict[str, str] | None:
        if not self.limits.proxies:
            return None
        with self._proxy_lock:
            proxy = self.limits.proxies[self._proxy_index % len(self.limits.proxies)]
            self._proxy_index += 1
        return {"http": proxy, "https": proxy}

    def get(self, url: str, **kwargs) -> Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> Response:
        # POST is an API call, not a crawl; robots.txt does not apply.
        return self.request("POST", url, check_robots=False, **kwargs)

    def request(self, method: str, url: str, *, check_robots: bool | None = None, **kwargs) -> Response:
        should_check = self.respect_robots if check_robots is None else check_robots
        if should_check and not _ROBOTS.allowed(url):
            log.info("robots.txt disallows %s", url)
            return Response(url=url, status_code=0, text="", ok=False, error="robots_disallowed")

        timeout = kwargs.pop("timeout", self.limits.timeout_seconds)
        last_error: str | None = None

        for attempt in range(self.limits.max_retries + 1):
            self.limiter.acquire()
            try:
                response = self.session.request(
                    method, url, timeout=timeout, proxies=self._next_proxy(), **kwargs
                )
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                self._backoff(attempt)
                continue

            if response.status_code in (429, 500, 502, 503, 504):
                last_error = f"http_{response.status_code}"
                retry_after = response.headers.get("Retry-After")
                self._backoff(attempt, retry_after)
                continue

            return Response(
                url=str(response.url),
                status_code=response.status_code,
                text=response.text,
                ok=response.ok,
            )

        return Response(url=url, status_code=0, text="", ok=False, error=last_error)

    @staticmethod
    def _backoff(attempt: int, retry_after: str | None = None) -> None:
        if retry_after:
            try:
                time.sleep(min(float(retry_after), 60.0))
                return
            except ValueError:
                pass
        time.sleep(min(2**attempt + random.uniform(0, 1), 30.0))
