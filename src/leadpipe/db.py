"""Postgres access layer.

Records are re-verified over time and `status` is mutable, so this is a real
database rather than flat files. Scrapers only ever touch `raw_records`; the
normalization pass is the sole writer of `coaches`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import get_config
from .models import Coach, RawRecord

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

_COACH_COLUMNS = [
    "full_name", "business_name", "primary_email", "secondary_emails", "phone",
    "instagram_handle", "instagram_followers", "website", "booking_url",
    "booking_platform", "booking_slot_minutes", "youtube_channel", "facebook_page",
    "linkedin_url", "running_meta_ads", "ad_first_seen_date", "ad_days_running",
    "niche", "location_country", "location_city", "source_modules", "first_seen_at",
    "last_verified_at", "qualification_score", "status", "reject_reason",
    "dedupe_email", "dedupe_booking_url", "dedupe_instagram", "dedupe_domain",
    "dedupe_name_key", "email_verify_status", "needs_manual_review", "review_reason",
    "price_point_usd", "team_language", "has_physical_address",
]


def _connect():
    """Open a psycopg connection. Imported lazily so pure-logic tests need no driver."""
    import psycopg
    from psycopg.rows import dict_row

    dsn = get_config().database_url
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set. Point it at Postgres or Supabase.")
    return psycopg.connect(dsn, row_factory=dict_row, autocommit=False)


@contextmanager
def connection() -> Iterator[Any]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Apply every migration in order. Statements are idempotent."""
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        raise RuntimeError(f"No migrations found in {MIGRATIONS_DIR}")
    with connection() as conn, conn.cursor() as cur:
        for path in files:
            log.info("applying migration %s", path.name)
            cur.execute(path.read_text())


# ---------------------------------------------------------------------------
# Staging
# ---------------------------------------------------------------------------


def insert_raw(records: Iterable[RawRecord], run_id: str) -> int:
    """Insert observations into staging. Returns the count of genuinely new rows.

    ON CONFLICT DO NOTHING against content_hash is what makes a weekly re-run
    idempotent: an unchanged page produces the same hash and is skipped.
    """
    rows = [
        (
            r.source_module,
            r.source_url,
            json.dumps(r.payload, default=str),
            r.content_hash(),
            run_id,
            r.collected_at,
        )
        for r in records
    ]
    if not rows:
        return 0
    with connection() as conn, conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO raw_records
                (source_module, source_url, payload, content_hash, run_id, collected_at)
            VALUES (%s, %s, %s::jsonb, %s, %s, %s)
            ON CONFLICT (content_hash) DO NOTHING
            """,
            rows,
        )
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


def fetch_unprocessed_raw(limit: int = 1000, module: str | None = None) -> list[dict[str, Any]]:
    sql = """
        SELECT id, source_module, source_url, payload, collected_at
        FROM raw_records
        WHERE processed_at IS NULL
    """
    params: list[Any] = []
    if module:
        sql += " AND source_module = %s"
        params.append(module)
    sql += " ORDER BY id LIMIT %s"
    params.append(limit)
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def mark_raw_processed(ids: list[int], error: str | None = None) -> None:
    if not ids:
        return
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE raw_records SET processed_at = now(), process_error = %s WHERE id = ANY(%s)",
            (error, ids),
        )


# ---------------------------------------------------------------------------
# Clean table
# ---------------------------------------------------------------------------


def find_existing(coach: Coach) -> dict[str, Any] | None:
    """Look up a matching row using the dedupe key priority order.

    Fuzzy name matching is intentionally not done here - it is resolved in the
    normalization pass, where it can be parked in merge_candidates for a human.
    """
    lookups = [
        ("dedupe_email", coach.dedupe_email),
        ("dedupe_booking_url", coach.dedupe_booking_url),
        ("dedupe_instagram", coach.dedupe_instagram),
        ("dedupe_domain", coach.dedupe_domain),
    ]
    with connection() as conn, conn.cursor() as cur:
        for column, value in lookups:
            if not value:
                continue
            cur.execute(
                f"SELECT * FROM coaches WHERE {column} = %s ORDER BY id LIMIT 1",
                (value,),
            )
            row = cur.fetchone()
            if row:
                return row
    return None


def fuzzy_candidates(coach: Coach, threshold: float = 0.9) -> list[dict[str, Any]]:
    """Trigram-similar names in the same niche. Candidates only, never auto-merged."""
    if not coach.dedupe_name_key or not coach.niche:
        return []
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT *, similarity(dedupe_name_key, %s) AS sim
            FROM coaches
            WHERE niche = %s
              AND dedupe_name_key IS NOT NULL
              AND similarity(dedupe_name_key, %s) >= %s
            ORDER BY sim DESC
            LIMIT 5
            """,
            (coach.dedupe_name_key, coach.niche, coach.dedupe_name_key, threshold),
        )
        return list(cur.fetchall())


def upsert_coach(coach: Coach) -> int:
    """Insert or update a coach row. Returns the row id."""
    row = coach.to_row()
    values = [row[c] for c in _COACH_COLUMNS]
    placeholders = ", ".join(["%s"] * len(_COACH_COLUMNS))
    columns = ", ".join(_COACH_COLUMNS)

    with connection() as conn, conn.cursor() as cur:
        if coach.id:
            assignments = ", ".join(f"{c} = %s" for c in _COACH_COLUMNS)
            cur.execute(
                f"UPDATE coaches SET {assignments}, updated_at = now() WHERE id = %s RETURNING id",
                [*values, coach.id],
            )
        else:
            cur.execute(
                f"INSERT INTO coaches ({columns}) VALUES ({placeholders}) RETURNING id",
                values,
            )
        result = cur.fetchone()
        coach.id = int(result["id"])
        return coach.id


def record_provenance(coach_id: int, provenance: dict[str, str], module: str, raw_id: int | None = None) -> None:
    """Log the source URL behind every field value.

    When a record looks wrong later, this is what lets you fix the scraper
    instead of guessing which module produced the bad value.
    """
    if not provenance:
        return
    rows = [(coach_id, field, None, module, source_url, raw_id) for field, source_url in provenance.items()]
    with connection() as conn, conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO field_provenance (coach_id, field, value, source_module, source_url, raw_record_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            rows,
        )


def record_score(coach_id: int, total: int, breakdown: dict[str, int], weights_version: str) -> None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO score_events (coach_id, total, breakdown, weights_v) VALUES (%s, %s, %s::jsonb, %s)",
            (coach_id, total, json.dumps(breakdown), weights_version),
        )


def flag_merge_candidate(coach_id: int, other_id: int, similarity: float, reason: str) -> None:
    if coach_id == other_id:
        return
    low, high = sorted((coach_id, other_id))
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO merge_candidates (coach_id, other_id, similarity, reason)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (coach_id, other_id) DO NOTHING
            """,
            (low, high, similarity, reason),
        )


def iter_coaches(where: str = "TRUE", params: tuple | list = (), batch: int = 500) -> Iterator[dict[str, Any]]:
    """Stream coach rows matching a filter."""
    offset = 0
    while True:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM coaches WHERE {where} ORDER BY id LIMIT %s OFFSET %s",
                [*params, batch, offset],
            )
            rows = list(cur.fetchall())
        if not rows:
            return
        yield from rows
        offset += batch


# ---------------------------------------------------------------------------
# Run log
# ---------------------------------------------------------------------------


def start_module_run(run_id: str, module: str) -> int:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO module_runs (run_id, source_module) VALUES (%s, %s) RETURNING id",
            (run_id, module),
        )
        return int(cur.fetchone()["id"])


def finish_module_run(
    run_log_id: int, status: str, found: int = 0, new: int = 0, error: str | None = None
) -> None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE module_runs
            SET finished_at = %s, status = %s, records_found = %s, records_new = %s, error = %s
            WHERE id = %s
            """,
            (datetime.now(UTC), status, found, new, error, run_log_id),
        )


def row_to_coach(row: dict[str, Any]) -> Coach:
    """Rehydrate a Coach from a database row."""
    known = set(_COACH_COLUMNS)
    data = {k: v for k, v in row.items() if k in known}
    coach = Coach(**data)
    coach.id = row.get("id")
    coach.secondary_emails = list(row.get("secondary_emails") or [])
    coach.source_modules = list(row.get("source_modules") or [])
    return coach
