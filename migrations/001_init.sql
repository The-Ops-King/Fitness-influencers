-- leadpipe schema: staging -> normalization -> clean table
-- Scrapers NEVER write to `coaches`. They write to `raw_records` only.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------------------
-- Staging. Every module lands here first. One row per observation.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw_records (
    id              BIGSERIAL PRIMARY KEY,
    source_module   TEXT        NOT NULL,
    source_url      TEXT,
    payload         JSONB       NOT NULL,
    -- sha256 over (module, normalized payload). Makes weekly re-runs idempotent.
    content_hash    TEXT        NOT NULL,
    run_id          TEXT        NOT NULL,
    collected_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at    TIMESTAMPTZ,
    process_error   TEXT,
    CONSTRAINT raw_records_content_hash_key UNIQUE (content_hash)
);

CREATE INDEX IF NOT EXISTS raw_records_unprocessed_idx
    ON raw_records (id) WHERE processed_at IS NULL;
CREATE INDEX IF NOT EXISTS raw_records_module_idx ON raw_records (source_module, collected_at DESC);

-- ---------------------------------------------------------------------------
-- Clean table. One row per coach.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS coaches (
    id                    BIGSERIAL PRIMARY KEY,
    full_name             TEXT,
    business_name         TEXT,
    primary_email         TEXT,
    secondary_emails      TEXT[]      NOT NULL DEFAULT '{}',
    phone                 TEXT,
    instagram_handle      TEXT,
    instagram_followers   INTEGER,
    website               TEXT,
    booking_url           TEXT,
    booking_platform      TEXT,   -- calendly | ghl | cal.com | acuity | koalendar | other
    booking_slot_minutes  INTEGER,
    youtube_channel       TEXT,
    facebook_page         TEXT,
    linkedin_url          TEXT,
    running_meta_ads      BOOLEAN     NOT NULL DEFAULT FALSE,
    ad_first_seen_date    DATE,
    ad_days_running       INTEGER,
    niche                 TEXT,
    location_country      TEXT,
    location_city         TEXT,
    source_modules        TEXT[]      NOT NULL DEFAULT '{}',
    first_seen_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_verified_at      TIMESTAMPTZ,
    qualification_score   INTEGER     NOT NULL DEFAULT 0,
    status                TEXT        NOT NULL DEFAULT 'raw',  -- raw|enriched|verified|rejected
    reject_reason         TEXT,
    -- Normalized dedupe keys, maintained by the normalization pass.
    dedupe_email          TEXT,
    dedupe_booking_url    TEXT,
    dedupe_instagram      TEXT,
    dedupe_domain         TEXT,
    dedupe_name_key       TEXT,
    email_verify_status   TEXT,   -- deliverable|risky|undeliverable|unknown|guessed
    needs_manual_review   BOOLEAN     NOT NULL DEFAULT FALSE,
    review_reason         TEXT,
    price_point_usd       NUMERIC,
    team_language         BOOLEAN,
    has_physical_address  BOOLEAN,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Partial uniques: dedupe keys are unique where present, NULLs unconstrained.
CREATE UNIQUE INDEX IF NOT EXISTS coaches_dedupe_email_uidx
    ON coaches (dedupe_email) WHERE dedupe_email IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS coaches_dedupe_booking_uidx
    ON coaches (dedupe_booking_url) WHERE dedupe_booking_url IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS coaches_dedupe_instagram_uidx
    ON coaches (dedupe_instagram) WHERE dedupe_instagram IS NOT NULL;
CREATE INDEX IF NOT EXISTS coaches_dedupe_domain_idx
    ON coaches (dedupe_domain) WHERE dedupe_domain IS NOT NULL;
CREATE INDEX IF NOT EXISTS coaches_name_key_trgm_idx
    ON coaches USING gin (dedupe_name_key gin_trgm_ops);
CREATE INDEX IF NOT EXISTS coaches_score_idx ON coaches (qualification_score DESC, status);

-- ---------------------------------------------------------------------------
-- Field-level provenance. Answers "where did this value come from".
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS field_provenance (
    id            BIGSERIAL PRIMARY KEY,
    coach_id      BIGINT      NOT NULL REFERENCES coaches(id) ON DELETE CASCADE,
    field         TEXT        NOT NULL,
    value         TEXT,
    source_module TEXT        NOT NULL,
    source_url    TEXT,
    raw_record_id BIGINT      REFERENCES raw_records(id) ON DELETE SET NULL,
    observed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS field_provenance_coach_idx ON field_provenance (coach_id, field);

-- ---------------------------------------------------------------------------
-- Score breakdown, so weight tuning can be replayed without a re-scrape.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS score_events (
    id         BIGSERIAL PRIMARY KEY,
    coach_id   BIGINT      NOT NULL REFERENCES coaches(id) ON DELETE CASCADE,
    total      INTEGER     NOT NULL,
    breakdown  JSONB       NOT NULL,
    weights_v  TEXT        NOT NULL,
    scored_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS score_events_coach_idx ON score_events (coach_id, scored_at DESC);

-- ---------------------------------------------------------------------------
-- Per-module run log. Isolates failures: Instagram dying must not halt anything.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS module_runs (
    id             BIGSERIAL PRIMARY KEY,
    run_id         TEXT        NOT NULL,
    source_module  TEXT        NOT NULL,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ,
    status         TEXT        NOT NULL DEFAULT 'running',  -- running|ok|failed
    records_found  INTEGER     NOT NULL DEFAULT 0,
    records_new    INTEGER     NOT NULL DEFAULT 0,
    error          TEXT
);
CREATE INDEX IF NOT EXISTS module_runs_module_idx ON module_runs (source_module, started_at DESC);

-- Fuzzy-match candidates parked for a human. Never auto-merged.
CREATE TABLE IF NOT EXISTS merge_candidates (
    id           BIGSERIAL PRIMARY KEY,
    coach_id     BIGINT      NOT NULL REFERENCES coaches(id) ON DELETE CASCADE,
    other_id     BIGINT      NOT NULL REFERENCES coaches(id) ON DELETE CASCADE,
    similarity   NUMERIC     NOT NULL,
    reason       TEXT        NOT NULL,
    resolved     BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT merge_candidates_pair_key UNIQUE (coach_id, other_id)
);
