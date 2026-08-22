"""ICP disqualifiers.

The ICP is a solo remote coach at roughly $5-15K/mo who takes their own calls.
None of that is in any database, so these rules work off the tells that are
observable: a physical address, team language, follower count, a checkout link
where a booking link should be, and coach-of-coaches vocabulary.
"""

from __future__ import annotations

import re

from .normalize import is_booking_url, is_checkout_url

# "We" language and roster pages: this is a team, not an operator.
TEAM_PATTERNS = [
    re.compile(r"\b(our team|meet the team|the team|our coaches|our trainers|our staff)\b", re.I),
    re.compile(r"\b(we are a|we're a|we help|our mission|our clients|join our team)\b", re.I),
    re.compile(r"\b(founder\s*&\s*ceo|head coach team|coaching staff)\b", re.I),
    re.compile(r"\b(agency|our agency|full[- ]service)\b", re.I),
]

# Physical premises. The single clearest in-person tell.
GYM_PATTERNS = [
    re.compile(
        r"\b\d{1,6}\s+[A-Za-z0-9.'\- ]{2,40}\s+"
        r"(street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln|way|"
        r"suite|ste|unit|highway|hwy|parkway|pkwy|court|ct|place|pl)\b\.?",
        re.I,
    ),
    re.compile(r"\b(our gym|our studio|our facility|visit us at|come train with us|drop[- ]in)\b", re.I),
    re.compile(r"\b(gym hours|studio hours|opening hours|class schedule|walk[- ]ins)\b", re.I),
    re.compile(r"\b(in[- ]person (only|training|sessions)|located (in|at))\b", re.I),
]

# Coach-of-coaches / business coaching. Different ICP, pollutes the list.
COACH_OF_COACHES_PATTERNS = [
    re.compile(r"\b(coach(es)? (of|for) coach(es)?|help (online )?coaches|for fitness (coaches|pros))\b", re.I),
    re.compile(r"\b(scale your (coaching )?business|grow your coaching business|6[- ]figure coach)\b", re.I),
    re.compile(r"\b(business coach|marketing coach|client acquisition|lead gen(eration)?|mastermind)\b", re.I),
    re.compile(r"\b(sign more clients|fill your calendar|done[- ]for[- ]you (ads|marketing))\b", re.I),
]

# Explicit call language on a site or bio. Scored, not filtered.
CALL_LANGUAGE_PATTERNS = [
    re.compile(
        r"\b(book a (call|consult|chat)|free (consult(ation)?|call)|discovery call|strategy call|"
        r"coaching call|apply (now|to work)|schedule a call|jump on a call|hop on a call|"
        r"clarity call|breakthrough call|1[- ]?on[- ]?1 call|zoom call|google meet)\b",
        re.I,
    )
]

# Remote delivery confirmation.
REMOTE_PATTERNS = [
    re.compile(r"\b(zoom|google meet|remote(ly)?|online coaching|virtual(ly)?|anywhere in the world)\b", re.I)
]

PRICE_RE = re.compile(r"[$£€]\s?(\d{1,3}(?:,\d{3})*|\d+)(?:\.\d{2})?\s*(?:/|per\s)?\s*(mo|month|week|wk)?", re.I)


def _any(patterns: list[re.Pattern[str]], text: str | None) -> bool:
    if not text:
        return False
    return any(p.search(text) for p in patterns)


def has_team_language(text: str | None) -> bool:
    return _any(TEAM_PATTERNS, text)


def has_physical_address(text: str | None) -> bool:
    return _any(GYM_PATTERNS, text)


def is_coach_of_coaches(text: str | None) -> bool:
    return _any(COACH_OF_COACHES_PATTERNS, text)


def has_call_language(text: str | None) -> bool:
    return _any(CALL_LANGUAGE_PATTERNS, text)


def mentions_remote_delivery(text: str | None) -> bool:
    return _any(REMOTE_PATTERNS, text)


def extract_price_usd(text: str | None) -> float | None:
    """Highest advertised price found. Sub-$500 and $10K+ both fall outside the band."""
    if not text:
        return None
    best: float | None = None
    for match in PRICE_RE.finditer(text):
        try:
            value = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        if 1 <= value <= 100_000 and (best is None or value > best):
            best = value
    return best




def disqualify(
    *,
    text: str | None,
    instagram_followers: int | None = None,
    booking_url: str | None = None,
    outbound_links: list[str] | None = None,
    max_followers: int = 150_000,
) -> str | None:
    """Return a reject reason, or None if the record survives.

    Hard filters only. Anything that is merely a weak signal belongs in scoring,
    not here - a rejected record stops costing enrichment money.
    """
    if instagram_followers is not None and instagram_followers > max_followers:
        return f"follower_count_above_{max_followers}"

    if has_physical_address(text):
        return "physical_gym_address"

    if has_team_language(text):
        return "team_or_agency_language"

    if is_coach_of_coaches(text):
        return "coach_of_coaches"

    # Pure program/app seller: money links but no call step anywhere.
    links = outbound_links or []
    has_call_step = is_booking_url(booking_url) or any(is_booking_url(u) for u in links)
    if not has_call_step and any(is_checkout_url(u) for u in links):
        return "checkout_no_call_step"

    return None
