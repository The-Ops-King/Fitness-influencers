import pytest

from leadpipe import normalize as n


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://CALENDLY.com/jess/30min?utm_source=ig", "https://calendly.com/jess/30min"),
        ("calendly.com/jess/30min/", "https://calendly.com/jess/30min"),
        ("http://www.Coach.com/apply?fbclid=abc", "https://coach.com/apply"),
        ("//cal.com/x", "https://cal.com/x"),
        ("not a url", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_url(raw, expected):
    assert n.normalize_url(raw) == expected


def test_normalize_url_keeps_meaningful_query():
    assert n.normalize_url("https://x.com/p?id=7&utm_medium=cpc") == "https://x.com/p?id=7"


def test_booking_url_strips_all_query_params():
    a = n.normalize_booking_url("https://calendly.com/jess/30min?month=2026-01&name=Bob")
    b = n.normalize_booking_url("https://calendly.com/jess/30min?utm_source=ig")
    assert a == b == "https://calendly.com/jess/30min"


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://blog.coach.co.uk/x", "coach.co.uk"),
        ("https://www.coach.com", "coach.com"),
        ("https://a.b.coach.com.au/x", "coach.com.au"),
        ("https://coach.io", "coach.io"),
    ],
)
def test_root_domain(url, expected):
    assert n.root_domain(url) == expected


@pytest.mark.parametrize(
    "url,platform",
    [
        ("https://calendly.com/jess/30min", "calendly"),
        ("https://cal.com/jess", "cal.com"),
        ("https://app.acuityscheduling.com/schedule.php?owner=1", "acuity"),
        ("https://koalendar.com/e/call", "koalendar"),
        ("https://api.leadconnectorhq.com/widget/booking/abc", "ghl"),
        ("https://link.msgsndr.com/widget/booking/xyz", "ghl"),
        # GHL white-labelled onto the coach's own domain.
        ("https://coachjess.com/widget/booking/abc123", "ghl"),
        ("https://coachjess.com/book", "other"),
        ("https://coachjess.com/discovery-call", "other"),
        ("https://coachjess.com/blog/post", None),
        ("https://instagram.com/jess", None),
    ],
)
def test_detect_booking_platform(url, platform):
    assert n.detect_booking_platform(url) == platform


def test_link_in_bio_and_checkout_detection():
    assert n.is_link_in_bio("https://stan.store/jessfit")
    assert n.is_link_in_bio("https://linktr.ee/jessfit")
    assert not n.is_link_in_bio("https://calendly.com/jess")
    assert n.is_checkout_url("https://buy.stripe.com/abc")
    assert not n.is_checkout_url("https://calendly.com/jess")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Jess.Ryan+ig@GMail.com", "jessryan@gmail.com"),
        ("jess@FitCo.com", "jess@fitco.com"),
        ("jess+a@fitco.com", "jess@fitco.com"),
        ("nope", None),
    ],
)
def test_email_dedupe_key(raw, expected):
    assert n.email_dedupe_key(raw) == expected


def test_extract_emails_skips_asset_filenames():
    text = "reach me at jess@fitco.com or see hero@2x.png"
    assert n.extract_emails(text) == ["jess@fitco.com"]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("@JessFit", "jessfit"),
        ("https://www.instagram.com/jess.fit/", "jess.fit"),
        ("https://instagram.com/p/Cabc123/", None),
        ("instagram.com/jessfit?hl=en", "jessfit"),
        ("not a handle!!", None),
    ],
)
def test_normalize_instagram_handle(raw, expected):
    assert n.normalize_instagram_handle(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("(512) 555-0134", "+15125550134"),
        ("+44 20 7946 0958", "+442079460958"),
        ("512.555.0134", "+15125550134"),
        ("12", None),
    ],
)
def test_normalize_phone(raw, expected):
    assert n.normalize_phone(raw) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Book a 45 minute strategy call", 45),
        ("30 Min Meeting", 30),
        ("1 hour discovery call", 60),
        ("Half-hour consult", 30),
        ("15-minute intro", 15),
        ("no duration here", None),
        # Guards against picking up "reply within 24 hours" style copy.
        ("30 min call. I reply within 24 hours.", 30),
    ],
)
def test_parse_slot_minutes(text, expected):
    assert n.parse_slot_minutes(text) == expected


def test_clean_person_name_strips_emoji_and_tagline():
    assert n.clean_person_name("jess ryan 🏋️ | Online Coach") == "Jess Ryan"
    assert n.clean_person_name("Sarah O'Brien - Fat Loss") == "Sarah O'Brien"
    assert n.clean_person_name("The Ultimate Fitness Transformation Academy Group") is None


def test_name_key_is_order_and_noise_insensitive():
    assert n.name_key("Jess Ryan Coaching") == n.name_key("Ryan, Jess (Online Coach)")


def test_name_similarity():
    assert n.name_similarity("Jess Ryan", "Jess Ryan Coaching") == 1.0
    assert n.name_similarity("Jess Ryan", "Mike Peters") < 0.5


@pytest.mark.parametrize(
    "text,expected",
    [
        ("prenatal and postpartum coaching", "prenatal"),
        ("fat loss transformation coach", "weight loss"),
        ("powerlifting strength coach", "strength"),
        ("hyrox endurance training", "endurance"),
        ("", None),
    ],
)
def test_detect_niche(text, expected):
    assert n.detect_niche(text) == expected


def test_detect_niche_prefers_specific_over_general():
    assert n.detect_niche("online coach fitness coach bikini contest prep") == "physique"


def test_root_path_collapses_so_domains_dedupe():
    assert n.normalize_url("https://site.com/") == n.normalize_url("https://site.com") == "https://site.com"
