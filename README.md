# leadpipe

Sources, enriches, deduplicates, and scores remote fitness coaches who run their
own sales calls. Output is a clean database plus three channel-ready CSVs.

**Instagram DM is the primary outreach channel**, so `instagram_dm.csv` is the
main deliverable and having a handle is itself a scoring signal. Email is a
bonus channel. The whole pipeline runs on free tiers — see [Running it free](#running-it-free).

## The ICP and why the build looks like this

The target is a remote fitness coach who sells over Zoom or Google Meet, does
roughly $5–15K/month, takes their own sales calls, and is the operator rather
than an agency.

None of those criteria exist in any database. They have to be inferred from
behavioral signals, which is what every module here is doing:

| ICP criterion | Observable proxy |
|---|---|
| Takes their own sales calls | **A public booking link.** The strongest signal in the pipeline. |
| Sells on a real call, not a 15-min chat | Booking slot duration of 30–60 minutes |
| Reachable at all | A public Instagram handle — the contact channel |
| Does $5–15K/month | Meta ad longevity — an ad running 90+ days is profitable |
| Is the operator, not a team | Absence of "we" language and a team roster |
| Remote, not in-person | Absence of a physical address |

Disqualifiers are hard filters, not score penalties, so a rejected record stops
costing enrichment money: in-person gyms (physical address), big names and
agencies (>150K followers, team pages), app/program sellers with a checkout link
and no call step, and coach-of-coaches business coaches.

## Architecture

```
6 source modules  ->  raw_records  ->  normalization  ->  coaches  ->  exports
   (independent)      (staging)        dedupe/merge      (clean)      (3 CSVs)
                                       + score
```

Non-negotiables baked into the design:

- **No scraper writes to the clean table.** Modules only ever append to
  `raw_records`. The normalization pass is the single writer of `coaches`.
- **Every field carries provenance.** `field_provenance` records the source URL
  behind each value, so a bad record later points at the scraper that made it
  instead of a guess.
- **Re-runs are idempotent.** Staging rows are keyed by a content hash with
  volatile keys excluded, so an unchanged page on next week's run is a no-op.
- **Modules are isolated.** A module that throws is logged failed on
  `module_runs` and the run continues. Instagram breaking costs you Instagram.
- **Proxy pools and rate limits are per module.** The Instagram residential pool
  is never shared with SERP.

## Source modules

| Module | Source | What makes it worth running |
|---|---|---|
| `m1_booking_serp` | SERP over booking domains × fitness terms | Highest signal, lowest anti-bot risk. 193 seed queries × deep pagination. Captures slot duration and the handle linked from the booking page. |
| `m2_meta_ads` | Meta Ad Library API | Self-qualifying: paying to book calls. Ad longevity is the best revenue proxy available. Follows the landing page for the booking link **and the Instagram handle**. |
| `m3_instagram` | Apify actors: hashtags, bio search, follow graph | **Optional, not in the free path.** Only needed to *discover* coaches via Instagram and to fetch follower counts — handles themselves are harvested free by the other modules. Follows the link-in-bio one hop deeper: Linktree, Stan Store, Beacons and Koji are the middle layer the booking link hides behind. |
| `m4_youtube` | YouTube Data API | Testimonial/transformation content, 500–20K subs. Parses descriptions and the About tab for booking links. |
| `m5_directories` | Trainerize, TrueCoach, Everfit, PT Distinction, Skool, Upwork, Bark, Thumbtack, service-area GBPs, podcast show notes | Platform exhaust, reached through site: searches. |
| `m6_testimonials` | Vendor case study / wall-of-love pages | Opportunistic. Everyone named is pre-qualified as someone who already buys software. |

Build order if sequencing is needed: modules 1 and 2 produce usable leads
fastest at the lowest anti-bot risk; module 3 has the most volume and the most
engineering overhead, so `run-all` puts it last.

## Deduplication

Matched in priority order — exact email, normalized booking URL (query params
stripped), Instagram handle, root domain, then fuzzy name + niche above 0.9
similarity. Fuzzy matches are **flagged in `merge_candidates` for a human, never
auto-merged**: two coaches really can share a name.

On merge, `source_modules` is unioned. Multi-source presence is itself a quality
signal and scores 10 points.

## Scoring

| Signal | Points |
|---|---|
| Has a working booking link | 30 |
| Has an Instagram handle | 15 |
| Booking slot 30 min or longer | 10 |
| Running Meta ads 90+ days | 20 |
| Running Meta ads under 90 days | 10 |
| Instagram followers 3K–75K | 15 |
| Followers 1K–3K or 75K–150K | 7 |
| Found by 2+ source modules | 10 |
| Verified deliverable email | 3 |
| Explicit call language | 5 |
| Team or agency language | −30 |
| Physical gym address | −40 |

Weights are `v2`, Instagram-first. A handle scores because it is the channel you
actually reach people on; a verified email dropped from 10 to 3 because it is now
a bonus. **Missing follower data is never a penalty** — the bands are a bonus when
module 3 has run, so a coach with a booking link and a handle clears the DM floor
on the free path without any Instagram scraping at all.

Score 50+ → outreach list. 30–49 → nurture pool for later re-verification.
Weights live in one dict (`scoring.WEIGHTS`) and every score is written to
`score_events` stamped with `WEIGHTS_VERSION`, so `leadpipe rescore` can retune
after the first run without re-scraping anything.

## Outputs

| Export | Contents | Floor |
|---|---|---|
| `instagram_dm.csv` | **The primary list.** Handle, profile URL, name, business, niche, booking link and platform, website, ad longevity, and one scraped personalization detail. Sorted by score descending, since DM is worked by hand. Needs no email. | 40 |
| `cold_email.csv` | **Verified emails only**, with merge fields: first name, niche, booking platform, and one scraped personalization detail | 50 |
| `meta_audience.csv` | Emails and phones in Meta's column format (`email,phone,fn,ln,country`), SHA-256 hashed by default | 70 |

The Meta floor is deliberately higher than the outreach floor: a noisy seed list
produces a fuzzier lookalike.

Exports are channel-agnostic CSVs. The CRM destination is undecided, and nothing
here is shaped to a specific vendor.

## Running it free

Every module except `m3_instagram` runs at no cost, and handles are harvested
without it:

| Piece | Free option | Cost |
|---|---|---|
| Storage | Supabase free tier, or the bundled `docker-compose.yml` | $0 |
| M1 / M5 / M6 SERP | `SERP_PROVIDER=auto` → Brave free tier if `BRAVE_API_KEY` is set, else DuckDuckGo with no key at all | $0 |
| M2 Meta Ad Library | The API is free; needs a developer app plus ID verification | $0 |
| M4 YouTube | 10,000 units/day free quota | $0 |
| Instagram **handles** | Harvested by M1, M2, M4, M5, M6 and the site scrape | $0 |
| Instagram **follower counts** | `leadpipe enrich-followers` — module 3 in enrichment-only mode | ~$2–12 one-off |

Leave `APIFY_TOKEN` and `EMAIL_VERIFIER` unset. Module 3 skips itself cleanly,
`cold_email.csv` comes out empty by design, and `instagram_dm.csv` fills.

`SERP_MAX_CALLS_PER_RUN` (default 2000) caps SERP calls across modules 1, 5 and 6
combined, so one sweep cannot burn a whole month's free allowance. Free tiers are
rate limited to roughly a query a second, so a full run takes hours rather than
minutes — that is the main thing you trade for the cost.

What you give up without follower data: the >150K big-name disqualifier and the
follower scoring bands do not fire. In practice agencies and big names still get
caught by the team-language filter — but see below, since getting the data back
is cheap.

### Follower counts without a discovery run

Module 3 has two modes, and they differ by orders of magnitude in cost:

| Mode | What it does | Billed Apify results |
|---|---|---|
| discovery (default) | Hashtag seeding, bio search, follow-graph expansion | ~17,500 per run |
| `enrich-followers` | One profile lookup per handle already in the database | 1 per handle |

Because handles are harvested free by the other modules, discovery is rarely
worth paying for. Enrichment-only is:

```bash
leadpipe enrich-followers --limit 2000   # one billed result per handle
leadpipe normalize                       # merge the counts in
leadpipe rescore                         # apply the follower bands
```

The work list is "records with a handle and no follower count", ordered by score
descending, so a `--limit` spends on the best records first. Once a record has a
count it drops off the list, so a weekly run never re-bills for it.
`APIFY_MAX_RESULTS_PER_RUN` (default 5000) is a hard ceiling on top of that.

Note the asymmetry this mode is built around: the >150K bound is a global
disqualifier, but the 1K floor is only a *discovery* filter. So enrichment
deliberately keeps out-of-band profiles rather than dropping them — otherwise a
400K-follower account already in the database would never get rejected.

## Setup

```bash
cp .env.example .env          # works as-is; keys only improve it
docker compose up -d db       # or point DATABASE_URL at Supabase
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
leadpipe init-db
```

## Running

```bash
# One module, capped, without writing anything
leadpipe run-module m1_booking_serp --limit 100 --dry-run

# Full weekly sweep - one module failing never stops the rest
leadpipe run-all

# Staging -> clean table: normalize, dedupe, merge, score
leadpipe normalize

# Site scrape, email discovery + verification, re-score
leadpipe enrich --limit 2000

# Follower counts for handles already in the database (module 3, enrich only)
leadpipe enrich-followers --limit 2000

# Retune weights without re-scraping
leadpipe rescore

# Write the three CSVs
leadpipe export --out exports/

leadpipe stats
```

Per-module options are JSON:

```bash
leadpipe run-module m1_booking_serp --option pages=15 --option 'countries=["us","ca"]'
leadpipe run-module m3_instagram --option 'hashtags=["onlinecoach"]' --option expand_hops=2
leadpipe run-module m6_testimonials --option 'seed_urls=["https://vendor.com/customers"]'
```

A weekly cron is just `run-all && normalize && enrich && export` — every stage
is idempotent.

## Email hygiene

Found addresses are verified through ZeroBounce, NeverBounce, or
MillionVerifier before export. Where no address is exposed, business emails are
pattern-guessed from the domain (free providers are skipped, since a guess there
is meaningless) — and **a guess is only ever promoted after it verifies as
deliverable**. Hard bounces are dropped before they reach a sending domain, and
`cold_email.csv` contains nothing whose `email_verify_status` is not
`deliverable`.

## Testing

```bash
pytest -q     # 183 tests, no network or database required
```

The scoring, dedupe, normalization, filtering, enrichment, export, and idempotency
logic is pure and fully covered. Network-bound code is exercised with stubs and
through the module registry contract.

## Operational notes

- `robots.txt` is respected on page crawls (fail-open on an unreachable file);
  vendor API calls are exempt, as they are APIs rather than crawls.
- Expect Instagram to be the module that breaks. It is isolated by design, runs
  last, and uses maintained Apify actors rather than raw requests.
- Deep SERP pagination stops as soon as a page returns nothing new, so
  exhausted queries stop costing money.

## Out of scope

Sending infrastructure. This pipeline stops at a clean, scored list.
