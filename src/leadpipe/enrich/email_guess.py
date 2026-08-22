"""Pattern-guessed business emails.

Guesses are always marked `guessed` and must clear verification before any of
them reaches a sending domain. An unverified guess on a cold domain is how you
burn the domain.
"""

from __future__ import annotations

import re

from ..normalize import normalize_email, root_domain, strip_accents

# Free mailbox providers: a guess here is meaningless, the local part is arbitrary.
FREE_PROVIDERS = {
    "gmail.com", "googlemail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "icloud.com", "aol.com", "live.com", "msn.com", "proton.me", "protonmail.com",
    "gmx.com", "yandex.com", "mail.com",
}

# Ordered by observed hit rate on solo-operator coaching domains.
PATTERNS = (
    "{first}",
    "hello",
    "info",
    "{first}.{last}",
    "coach",
    "team",
    "contact",
    "{first}{last}",
    "{f}{last}",
    "support",
)


def _tokens(full_name: str | None) -> tuple[str, str]:
    if not full_name:
        return "", ""
    cleaned = re.sub(r"[^A-Za-z\s]", " ", strip_accents(full_name)).strip().lower()
    parts = [p for p in cleaned.split() if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def guess_emails(full_name: str | None, website: str | None, limit: int = 6) -> list[str]:
    """Candidate business emails for a domain. Verify before sending, always."""
    domain = root_domain(website)
    if not domain or domain in FREE_PROVIDERS:
        return []

    first, last = _tokens(full_name)
    out: list[str] = []
    for pattern in PATTERNS:
        if "{first}" in pattern and not first:
            continue
        if "{last}" in pattern and not last:
            continue
        local = pattern.format(first=first, last=last, f=first[:1] if first else "")
        candidate = normalize_email(f"{local}@{domain}")
        if candidate and candidate not in out:
            out.append(candidate)
        if len(out) >= limit:
            break
    return out
