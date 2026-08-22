"""Normalization primitives.

Everything in this module is pure and dependency-free so it can be unit tested
without a network or a database. The dedupe keys the whole pipeline depends on
are produced here.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# --------------------------------------------------------------------------
# Booking platforms
# --------------------------------------------------------------------------

BOOKING_DOMAINS: dict[str, str] = {
    "calendly.com": "calendly",
    "cal.com": "cal.com",
    "app.acuityscheduling.com": "acuity",
    "acuityscheduling.com": "acuity",
    "koalendar.com": "koalendar",
    "msgsndr.com": "ghl",
    "leadconnectorhq.com": "ghl",
    "gohighlevel.com": "ghl",
    "tidycal.com": "other",
    "savvycal.com": "other",
    "youcanbook.me": "other",
    "hubspot.com": "other",
    "meetings.hubspot.com": "other",
    "oncehub.com": "other",
    "scheduleonce.com": "other",
    "setmore.com": "other",
    "picktime.com": "other",
    "zcal.co": "other",
}

# GHL-hosted calendars on a client's own domain use these path fingerprints.
GHL_PATH_MARKERS = ("/widget/booking/", "/widget/bookings/", "/widget/group/", "/widget/form/")

# Link-in-bio hubs. The booking link almost always sits one hop behind these.
LINK_IN_BIO_DOMAINS = {
    "linktr.ee",
    "stan.store",
    "beacons.ai",
    "beacons.page",
    "withkoji.com",
    "koji.to",
    "flowcode.com",
    "milkshake.app",
    "shorby.com",
    "campsite.bio",
    "linkin.bio",
    "later.com",
    "solo.to",
    "bio.link",
    "carrd.co",
    "taplink.cc",
    "allmylinks.com",
}

# Checkout/self-serve purchase = no call step = disqualifier signal.
CHECKOUT_DOMAINS = {
    "checkout.stripe.com",
    "buy.stripe.com",
    "gumroad.com",
    "paypal.com",
    "samcart.com",
    "thrivecart.com",
    "kajabi.com",
    "teachable.com",
    "podia.com",
    "shopify.com",
    "square.site",
    "squareup.com",
}

TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "gbraid",
    "wbraid",
    "msclkid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "igsh",
    "ref",
    "ref_src",
    "referrer",
    "source",
    "_ga",
    "_gl",
    "yclid",
    "ttclid",
    "li_fat_id",
    "epik",
    "s_kwcid",
    "hsa_acc",
    "hsa_cam",
    "hsa_grp",
    "hsa_ad",
    "hsa_src",
    "hsa_tgt",
    "hsa_kw",
    "hsa_mt",
    "hsa_net",
    "hsa_ver",
}

# Two-label public suffixes we care about for root-domain extraction. A full PSL
# is overkill here; these cover the target countries plus common coach domains.
MULTI_PART_SUFFIXES = {
    "co.uk", "org.uk", "me.uk", "ac.uk", "gov.uk", "ltd.uk", "plc.uk", "net.uk",
    "com.au", "net.au", "org.au", "edu.au", "id.au", "asn.au",
    "co.nz", "net.nz", "org.nz",
    "com.br", "com.mx", "com.ar", "com.co", "com.sg", "com.my", "com.ph",
    "co.za", "co.in", "co.il", "co.jp", "co.kr", "co.th",
    "com.tr", "com.tw", "com.hk", "com.cn", "com.pk", "com.ua",
}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}")
# Deliberately conservative: NANP and E.164-ish, avoids matching zip codes and prices.
PHONE_RE = re.compile(
    r"(?:(?<!\d)(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}(?!\d))"
    r"|(?:\+\d{1,3}[\s.\-]?\d{2,4}[\s.\-]?\d{3,4}[\s.\-]?\d{3,4})"
)
IG_URL_RE = re.compile(r"instagram\.com/([A-Za-z0-9_.]{1,30})", re.I)
YT_URL_RE = re.compile(r"youtube\.com/(?:channel/|c/|@)?([A-Za-z0-9_\-@.]{2,80})", re.I)

# Words that appear in an Instagram URL path but are not handles.
IG_RESERVED = {"p", "reel", "reels", "stories", "explore", "tv", "accounts", "direct", "s"}

# A netloc must look like a hostname: labels, dots, an alphabetic TLD, no spaces.
_HOSTNAME_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}(?::\d{1,5})?$")


# --------------------------------------------------------------------------
# URLs
# --------------------------------------------------------------------------


def normalize_url(url: str | None, *, strip_query: bool = False) -> str | None:
    """Canonical form of a URL: lowercase host, no www, no tracking params, no fragment.

    `strip_query=True` drops the query entirely - used for booking URLs, where
    prefill params (?name=, ?email=, UTMs) would otherwise split one page into
    many apparent records.
    """
    if not url:
        return None
    raw = url.strip()
    if not raw:
        return None
    if raw.startswith("//"):
        raw = "https:" + raw
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    if not parts.netloc:
        return None

    scheme = "https"
    host = parts.netloc.lower()
    if "@" in host:  # strip userinfo
        host = host.rsplit("@", 1)[1]
    if host.startswith("www."):
        host = host[4:]
    host = host.removesuffix(":80").removesuffix(":443")
    # Bare text like "not a url" parses as a netloc; reject it here.
    if not _HOSTNAME_RE.match(host):
        return None

    path = re.sub(r"/{2,}", "/", parts.path or "")
    # A bare root must collapse to "" so site.com/ and site.com are one record.
    path = path.rstrip("/")

    if strip_query:
        query = ""
    else:
        kept = [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=False)
            if k.lower() not in TRACKING_PARAMS and not k.lower().startswith("utm_")
        ]
        query = urlencode(sorted(kept))

    return urlunsplit((scheme, host, path, query, ""))


def normalize_booking_url(url: str | None) -> str | None:
    """Booking URL canonicalized for dedupe: query params stripped entirely."""
    return normalize_url(url, strip_query=True)


def host_of(url: str | None) -> str | None:
    normalized = normalize_url(url)
    if not normalized:
        return None
    return urlsplit(normalized).netloc or None


def root_domain(url: str | None) -> str | None:
    """Registrable domain, e.g. https://blog.coach.co.uk/x -> coach.co.uk."""
    host = host_of(url)
    if not host:
        return None
    host = host.split(":")[0]
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    last_two = ".".join(labels[-2:])
    if last_two in MULTI_PART_SUFFIXES and len(labels) >= 3:
        return ".".join(labels[-3:])
    return last_two


def detect_booking_platform(url: str | None) -> str | None:
    """Map a URL to a booking platform, or None if it is not a booking link."""
    host = host_of(url)
    if not host:
        return None
    normalized = normalize_url(url) or ""
    path = urlsplit(normalized).path.lower()

    for domain, platform in BOOKING_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return platform
    # GHL white-labelled onto a client's own domain.
    if any(marker in path for marker in GHL_PATH_MARKERS):
        return "ghl"
    # Self-hosted booking page on the coach's own site.
    if re.search(r"/(book|booking|schedule|apply|call|consult(ation)?|discovery)(-[a-z]+)?/?$", path):
        return "other"
    return None


def is_booking_url(url: str | None) -> bool:
    return detect_booking_platform(url) is not None


def is_link_in_bio(url: str | None) -> bool:
    host = host_of(url)
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in LINK_IN_BIO_DOMAINS)


def is_checkout_url(url: str | None) -> bool:
    host = host_of(url)
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in CHECKOUT_DOMAINS)


# --------------------------------------------------------------------------
# Emails, phones, handles
# --------------------------------------------------------------------------


def normalize_email(email: str | None) -> str | None:
    """Lowercase and trim. Preserves the address as it must be sent to."""
    if not email:
        return None
    cleaned = email.strip().strip("<>").strip().lower()
    if not EMAIL_RE.fullmatch(cleaned):
        return None
    return cleaned


def email_dedupe_key(email: str | None) -> str | None:
    """Dedupe-only form: plus-tags dropped, dots removed on Google mailboxes.

    Never use this as a send-to address; it is a matching key only.
    """
    normalized = normalize_email(email)
    if not normalized:
        return None
    local, _, domain = normalized.partition("@")
    local = local.split("+", 1)[0]
    if domain in {"gmail.com", "googlemail.com"}:
        local = local.replace(".", "")
        domain = "gmail.com"
    if not local:
        return None
    return f"{local}@{domain}"


def extract_emails(text: str | None) -> list[str]:
    if not text:
        return []
    seen: dict[str, None] = {}
    for match in EMAIL_RE.findall(text):
        normalized = normalize_email(match)
        # Image filenames and asset hashes routinely match the email regex.
        if normalized and not normalized.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
            seen.setdefault(normalized, None)
    return list(seen)


def normalize_phone(phone: str | None, default_country_code: str = "1") -> str | None:
    """E.164-ish normalization. Good enough for Meta custom audience hashing."""
    if not phone:
        return None
    digits = re.sub(r"[^\d+]", "", phone)
    if not digits:
        return None
    if digits.startswith("+"):
        digits = "+" + re.sub(r"\D", "", digits[1:])
    else:
        bare = re.sub(r"\D", "", digits)
        if len(bare) == 10:
            digits = f"+{default_country_code}{bare}"
        elif len(bare) == 11 and bare.startswith("1"):
            digits = f"+{bare}"
        else:
            digits = f"+{bare}"
    body = digits[1:]
    if not 8 <= len(body) <= 15:
        return None
    return digits


def extract_phones(text: str | None) -> list[str]:
    if not text:
        return []
    out: dict[str, None] = {}
    for match in PHONE_RE.findall(text):
        normalized = normalize_phone(match)
        if normalized:
            out.setdefault(normalized, None)
    return list(out)


def normalize_instagram_handle(value: str | None) -> str | None:
    """Accepts a handle, an @handle, or any instagram.com URL."""
    if not value:
        return None
    candidate = value.strip()
    if "instagram.com" in candidate.lower():
        match = IG_URL_RE.search(candidate)
        if not match:
            return None
        candidate = match.group(1)
    candidate = candidate.lstrip("@").strip().strip("/").lower()
    candidate = candidate.split("?")[0].split("/")[0]
    if not candidate or candidate in IG_RESERVED:
        return None
    if not re.fullmatch(r"[a-z0-9_.]{1,30}", candidate):
        return None
    return candidate


def normalize_youtube_channel(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip()
    if candidate.startswith("@"):
        return candidate.lower()
    if "youtube.com" not in candidate.lower():
        return None
    normalized = normalize_url(candidate, strip_query=True)
    return normalized


# --------------------------------------------------------------------------
# Names
# --------------------------------------------------------------------------

_NAME_NOISE = re.compile(
    r"\b(coach|coaching|online|fitness|nutrition|trainer|training|pt|personal|"
    r"llc|ltd|inc|co|the|official|real|iam|im)\b",
    re.I,
)


def strip_accents(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))


def clean_person_name(value: str | None) -> str | None:
    """Turn a scraped display name into a person name, or None if it is not one.

    Scraped names carry emoji, pipes, and taglines: 'Jess Ryan 🏋️ | Online Coach'.
    """
    if not value:
        return None
    text = strip_accents(value)
    text = re.split(r"[|•·\-–—/(]", text)[0]
    text = re.sub(r"[^\w\s'.]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    tokens = [t for t in text.split() if t]
    if not 1 <= len(tokens) <= 4:
        return None
    if not any(len(t) > 1 for t in tokens):
        return None
    return " ".join(t.capitalize() if t.islower() or t.isupper() else t for t in tokens)


def name_key(value: str | None) -> str | None:
    """Order-insensitive, noise-free key for fuzzy name matching."""
    if not value:
        return None
    text = strip_accents(value).lower()
    text = _NAME_NOISE.sub(" ", text)
    text = re.sub(r"[^a-z\s]", " ", text)
    tokens = sorted({t for t in text.split() if len(t) > 1})
    if not tokens:
        return None
    return " ".join(tokens)


def name_similarity(a: str | None, b: str | None) -> float:
    key_a, key_b = name_key(a), name_key(b)
    if not key_a or not key_b:
        return 0.0
    return difflib.SequenceMatcher(None, key_a, key_b).ratio()


# --------------------------------------------------------------------------
# Booking slot duration
# --------------------------------------------------------------------------

_SLOT_PATTERNS = [
    (re.compile(r"(\d{1,3})\s*(?:-|\s)?\s*(?:min(?:ute)?s?\b)", re.I), 1),
    (re.compile(r"(\d{1,2})\s*(?:-|\s)?\s*(?:hour|hr)s?\b", re.I), 60),
    (re.compile(r"\b(?:half[\s-]?hour)\b", re.I), None),
]


def parse_slot_minutes(text: str | None) -> int | None:
    """Pull a call duration out of booking-page copy.

    Duration is a scoring signal: 15-minute slots skew low ticket, 30-60 minute
    slots indicate a real sales call.
    """
    if not text:
        return None
    if re.search(r"\bhalf[\s-]?hour\b", text, re.I):
        return 30
    candidates: list[int] = []
    for pattern, multiplier in _SLOT_PATTERNS[:2]:
        for match in pattern.finditer(text):
            try:
                value = int(match.group(1)) * multiplier
            except (ValueError, TypeError):
                continue
            if 5 <= value <= 240:
                candidates.append(value)
    if not candidates:
        return None
    # Booking pages list the offered slot before any "within 24 hours" copy;
    # the smallest plausible duration is the safest read.
    return min(candidates)


# --------------------------------------------------------------------------
# Niche
# --------------------------------------------------------------------------

NICHE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "weight loss": ("weight loss", "fat loss", "lose weight", "shred", "cutting", "slim down"),
    "strength": ("strength", "powerlifting", "barbell", "muscle build", "hypertrophy", "bulking"),
    "prenatal": ("prenatal", "postpartum", "pregnancy", "postnatal", "mom body", "mum body"),
    "endurance": ("marathon", "triathlon", "endurance", "hyrox", "running coach", "cycling"),
    "nutrition": ("macro", "nutrition", "meal plan", "dietitian", "nutritionist"),
    "menopause": ("menopause", "perimenopause", "hormone"),
    "physique": ("bikini", "bodybuilding", "physique", "npc", "contest prep", "stage prep"),
    "general": ("online coach", "fitness coach", "personal trainer", "transformation"),
}


def detect_niche(*texts: str | None) -> str | None:
    blob = " ".join(t.lower() for t in texts if t)
    if not blob:
        return None
    best: tuple[str, int] | None = None
    for niche, keywords in NICHE_KEYWORDS.items():
        hits = sum(blob.count(k) for k in keywords)
        if hits and (best is None or hits > best[1]):
            best = (niche, hits)
    # "general" only wins when nothing more specific matched.
    if best and best[0] == "general":
        for niche, keywords in NICHE_KEYWORDS.items():
            if niche == "general":
                continue
            if any(k in blob for k in keywords):
                return niche
    return best[0] if best else None
