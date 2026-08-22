"""Query vocabulary shared across modules.

Kept in one place so a tuning pass on the term list touches one file. The
booking-domain x fitness-term cross product is what Module 1 iterates.
"""

from __future__ import annotations

from itertools import product

# Booking domains to fingerprint. GHL clients also run calendars on their own
# domains, which Module 1 catches via the /widget/booking/ path marker instead.
BOOKING_SITE_QUERIES = [
    "calendly.com",
    "cal.com",
    "app.acuityscheduling.com",
    "koalendar.com",
    "msgsndr.com",
    "api.leadconnectorhq.com",
    "link.msgsndr.com",
    "tidycal.com",
    "go.oncehub.com",
]

FITNESS_TERMS = [
    "online fitness coach",
    "online coaching",
    "body transformation",
    "fat loss coaching",
    "nutrition coaching",
    "macro coaching",
    "strength coach",
    "physique coach",
    "prenatal fitness coach",
    "postpartum coach",
    "hyrox coach",
    "bikini prep coach",
    "menopause fitness coach",
]

CALL_TERMS = [
    "strategy call",
    "discovery call",
    "coaching call",
    "free consult",
    "consultation call",
    "application call",
    "intro call",
    "clarity call",
]

# Terms that pull the coach's own site rather than the booking host.
SITE_CALL_PHRASES = [
    '"book a call" "online coaching"',
    '"apply to work with me" fitness coach',
    '"schedule your free consultation" online fitness',
    '"1:1 coaching" "book a call" fitness',
]

INSTAGRAM_HASHTAGS = [
    "onlinecoach",
    "onlinefitnesscoach",
    "nutritioncoach",
    "transformationcoach",
    "onlinepersonaltrainer",
    "fatlosscoach",
    "macrocoach",
    "femalefitnesscoach",
    "onlinecoaching",
    "fitnessbusiness",
]

INSTAGRAM_BIO_TERMS = [
    "online coach",
    "online fitness coach",
    "nutrition coach",
    "transformation coach",
    "1:1 coaching",
]

YOUTUBE_QUERIES = [
    "online coaching client results",
    "client transformation online coaching",
    "before and after coaching results",
    "my online coaching client transformation",
    "12 week transformation client",
    "online fitness coaching results",
]

META_AD_SEARCH_TERMS = [
    "online fitness coaching",
    "body transformation coaching",
    "fat loss coaching",
    "nutrition coaching",
    "1:1 online coaching",
    "free consultation fitness",
    "book a call fitness coach",
]

# Directory and marketplace surfaces with public coach profiles.
DIRECTORY_SITES = [
    ("trainerize.me", "trainerize"),
    ("trainerize.com", "trainerize"),
    ("truecoach.co", "truecoach"),
    ("everfit.io", "everfit"),
    ("ptdistinction.com", "ptdistinction"),
    ("skool.com", "skool"),
    ("upwork.com", "upwork"),
    ("bark.com", "bark"),
    ("thumbtack.com", "thumbtack"),
]

DIRECTORY_TERMS = [
    "online fitness coach",
    "online personal trainer",
    "nutrition coach",
    "fitness coaching community",
]

# Vendors whose customers are, by definition, coaches who already buy software.
TESTIMONIAL_SEED_QUERIES = [
    'fitness coach testimonial "book more calls" case study',
    '"case study" online fitness coach CRM',
    '"success story" fitness coaching software',
    'coaching software "wall of love" fitness',
]

# Google Business Profiles with a service area rather than a storefront.
GBP_QUERIES = [
    "online fitness coaching service area business",
    '"serves customers" online personal training',
]

PODCAST_QUERIES = [
    "fitness business podcast show notes guest coach booking",
    '"episode" online fitness coach interview "book a call"',
]


def booking_link_queries() -> list[str]:
    """site: booking-domain x (fitness term | call term) cross product."""
    queries: list[str] = []
    for site, term in product(BOOKING_SITE_QUERIES, FITNESS_TERMS):
        queries.append(f'site:{site} "{term}"')
    for site, term in product(BOOKING_SITE_QUERIES, CALL_TERMS):
        queries.append(f'site:{site} "{term}" fitness')
    queries.extend(SITE_CALL_PHRASES)
    return queries


def directory_queries() -> list[str]:
    return [
        f'site:{site} "{term}"'
        for (site, _platform), term in product(DIRECTORY_SITES, DIRECTORY_TERMS)
    ]
