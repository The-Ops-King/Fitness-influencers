"""Email verification.

Hard bounces are discarded before they ever hit a sending domain. Provider is
selectable so the pipeline is not married to one vendor's pricing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..config import get_config
from ..http import HttpClient
from ..normalize import normalize_email

log = logging.getLogger(__name__)

# Normalized across vendors: deliverable | risky | undeliverable | unknown
_ZEROBOUNCE_MAP = {
    "valid": "deliverable",
    "catch-all": "risky",
    "unknown": "unknown",
    "invalid": "undeliverable",
    "spamtrap": "undeliverable",
    "abuse": "undeliverable",
    "do_not_mail": "undeliverable",
}
_NEVERBOUNCE_MAP = {
    "valid": "deliverable",
    "catchall": "risky",
    "unknown": "unknown",
    "invalid": "undeliverable",
    "disposable": "undeliverable",
}
_MILLIONVERIFIER_MAP = {
    "ok": "deliverable",
    "catch_all": "risky",
    "unknown": "unknown",
    "invalid": "undeliverable",
    "disposable": "undeliverable",
    "error": "unknown",
}

SENDABLE = {"deliverable"}


@dataclass
class VerifyResult:
    email: str
    status: str  # deliverable | risky | undeliverable | unknown
    provider: str
    raw: dict | None = None

    @property
    def sendable(self) -> bool:
        return self.status in SENDABLE


def verify_email(email: str, client: HttpClient | None = None) -> VerifyResult:
    """Verify one address. Returns `unknown` when no provider is configured."""
    normalized = normalize_email(email)
    if not normalized:
        return VerifyResult(email=email, status="undeliverable", provider="local")

    config = get_config()
    provider = config.email_verifier
    key = config.email_verifier_key

    if provider == "none" or not key:
        return VerifyResult(email=normalized, status="unknown", provider="none")

    http = client or HttpClient("web", respect_robots=False)

    if provider == "zerobounce":
        response = http.get(
            "https://api.zerobounce.net/v2/validate",
            params={"api_key": key, "email": normalized},
        )
        data = response.json()
        return VerifyResult(
            normalized, _ZEROBOUNCE_MAP.get(str(data.get("status", "")).lower(), "unknown"), provider, data
        )

    if provider == "neverbounce":
        response = http.get(
            "https://api.neverbounce.com/v4/single/check",
            params={"key": key, "email": normalized},
        )
        data = response.json()
        return VerifyResult(
            normalized, _NEVERBOUNCE_MAP.get(str(data.get("result", "")).lower(), "unknown"), provider, data
        )

    if provider == "millionverifier":
        response = http.get(
            "https://api.millionverifier.com/api/v3/",
            params={"api": key, "email": normalized},
        )
        data = response.json()
        return VerifyResult(
            normalized, _MILLIONVERIFIER_MAP.get(str(data.get("resultcode_text", data.get("result", ""))).lower(), "unknown"), provider, data
        )

    log.warning("unknown email verifier %r, skipping verification", provider)
    return VerifyResult(normalized, "unknown", provider)


def pick_primary(results: list[VerifyResult]) -> VerifyResult | None:
    """Best address from a set: deliverable beats risky beats unknown."""
    order = {"deliverable": 0, "risky": 1, "unknown": 2, "undeliverable": 3}
    usable = [r for r in results if r.status != "undeliverable"]
    if not usable:
        return None
    return sorted(usable, key=lambda r: order.get(r.status, 9))[0]
