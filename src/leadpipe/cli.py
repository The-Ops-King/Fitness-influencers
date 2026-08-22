"""Command line entry point.

    leadpipe init-db
    leadpipe run-module m1_booking_serp --limit 500
    leadpipe run-all
    leadpipe normalize
    leadpipe enrich --limit 1000
    leadpipe rescore
    leadpipe export --out exports/
    leadpipe stats
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from pathlib import Path

import click

from . import db
from .config import get_config
from .exports import export_cold_email, export_instagram_dm, export_meta_audience
from .modules import MODULE_REGISTRY, ModuleContext, run_module
from .pipeline import run_enrichment, run_rescore
from .staging import run_normalization

DEFAULT_ORDER = [
    # Fastest to usable leads and lowest anti-bot risk first.
    "m1_booking_serp",
    "m2_meta_ads",
    "m4_youtube",
    "m5_directories",
    "m6_testimonials",
    # Highest volume, most likely to break. Runs last so a block costs nothing.
    "m3_instagram",
]


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Debug logging.")
def cli(verbose: bool) -> None:
    """Lead sourcing pipeline for remote fitness coaches who run their own calls."""
    _setup_logging(verbose)


@cli.command("init-db")
def init_db_cmd() -> None:
    """Apply the schema migrations."""
    db.init_db()
    click.echo("schema applied")


@cli.command("run-module")
@click.argument("module", type=click.Choice(sorted(MODULE_REGISTRY)))
@click.option("--limit", type=int, default=None, help="Stop after N observations.")
@click.option("--run-id", default=None, help="Group this run with others.")
@click.option("--dry-run", is_flag=True, help="Do not write to the database.")
@click.option("--option", "options", multiple=True, metavar="KEY=JSON", help="Module option override.")
def run_module_cmd(module: str, limit: int | None, run_id: str | None, dry_run: bool, options: tuple[str, ...]) -> None:
    """Run one source module into staging."""
    ctx = ModuleContext(
        run_id=run_id or uuid.uuid4().hex[:12],
        limit=limit,
        dry_run=dry_run,
        options=_parse_options(options),
    )
    summary = run_module(module, ctx)
    click.echo(json.dumps(summary, indent=2))


@cli.command("run-all")
@click.option("--limit", type=int, default=None, help="Per-module observation cap.")
@click.option("--run-id", default=None)
@click.option("--dry-run", is_flag=True)
@click.option("--skip", multiple=True, help="Module to skip. Repeatable.")
def run_all_cmd(limit: int | None, run_id: str | None, dry_run: bool, skip: tuple[str, ...]) -> None:
    """Run every source module. One module failing never stops the rest."""
    run = run_id or uuid.uuid4().hex[:12]
    summaries = []
    for module in DEFAULT_ORDER:
        if module in skip:
            continue
        ctx = ModuleContext(run_id=run, limit=limit, dry_run=dry_run)
        summaries.append(run_module(module, ctx))
    click.echo(json.dumps({"run_id": run, "modules": summaries}, indent=2))


@cli.command("normalize")
@click.option("--batch-size", type=int, default=1000)
@click.option("--module", default=None, help="Only normalize rows from this module.")
@click.option("--dry-run", is_flag=True)
def normalize_cmd(batch_size: int, module: str | None, dry_run: bool) -> None:
    """Move staging rows into the clean table: dedupe, merge, score."""
    click.echo(json.dumps(run_normalization(batch_size, module, dry_run), indent=2))


@cli.command("enrich")
@click.option("--limit", type=int, default=None)
@click.option("--status", "statuses", multiple=True, default=("raw",))
def enrich_cmd(limit: int | None, statuses: tuple[str, ...]) -> None:
    """Site scrape, email discovery, verification, and re-score."""
    click.echo(json.dumps(run_enrichment(limit, tuple(statuses)), indent=2))


@cli.command("rescore")
@click.option("--where", default="status <> 'rejected'", help="SQL predicate over `coaches`.")
def rescore_cmd(where: str) -> None:
    """Re-apply the current weights without re-scraping."""
    click.echo(json.dumps(run_rescore(where), indent=2))


@cli.command("export")
@click.option("--out", type=click.Path(path_type=Path), default=Path("exports"))
@click.option("--email-floor", type=int, default=None, help="Default: OUTREACH_SCORE_FLOOR.")
@click.option("--dm-floor", type=int, default=40)
@click.option("--audience-floor", type=int, default=70)
@click.option("--no-hash", is_flag=True, help="Write the Meta audience unhashed.")
def export_cmd(out: Path, email_floor: int | None, dm_floor: int, audience_floor: int, no_hash: bool) -> None:
    """Write the three channel CSVs."""
    config = get_config()
    floor = email_floor if email_floor is not None else config.outreach_score_floor

    # Materialized once: three passes over a generator would drain it.
    rows = list(db.iter_coaches("status <> 'rejected'"))

    results = {
        "cold_email": export_cold_email(rows, out / "cold_email.csv", min_score=floor),
        "instagram_dm": export_instagram_dm(rows, out / "instagram_dm.csv", min_score=dm_floor),
        "meta_audience": export_meta_audience(
            rows, out / "meta_audience.csv", min_score=audience_floor, hash_values=not no_hash
        ),
    }
    click.echo(json.dumps({k: {"path": str(v.path), "rows": v.rows} for k, v in results.items()}, indent=2))


@cli.command("stats")
def stats_cmd() -> None:
    """Counts by status, tier, and source module."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT status, count(*) AS n FROM coaches GROUP BY status ORDER BY n DESC")
        by_status = {r["status"]: r["n"] for r in cur.fetchall()}

        cur.execute(
            """
            SELECT CASE
                     WHEN qualification_score >= 50 THEN 'outreach'
                     WHEN qualification_score >= 30 THEN 'nurture'
                     ELSE 'discard'
                   END AS tier,
                   count(*) AS n
            FROM coaches WHERE status <> 'rejected' GROUP BY 1 ORDER BY n DESC
            """
        )
        by_tier = {r["tier"]: r["n"] for r in cur.fetchall()}

        cur.execute(
            "SELECT unnest(source_modules) AS m, count(*) AS n FROM coaches GROUP BY 1 ORDER BY n DESC"
        )
        by_module = {r["m"]: r["n"] for r in cur.fetchall()}

        cur.execute("SELECT count(*) AS n FROM coaches WHERE cardinality(source_modules) >= 2")
        multi_source = cur.fetchone()["n"]

        cur.execute("SELECT count(*) AS n FROM raw_records WHERE processed_at IS NULL")
        pending = cur.fetchone()["n"]

    click.echo(
        json.dumps(
            {
                "by_status": by_status,
                "by_tier": by_tier,
                "by_source_module": by_module,
                "multi_source_records": multi_source,
                "staging_pending": pending,
            },
            indent=2,
        )
    )


def _parse_options(pairs: tuple[str, ...]) -> dict:
    """Parse --option key=json into a dict. Bare strings are accepted as-is."""
    out: dict = {}
    for pair in pairs:
        key, _, value = pair.partition("=")
        if not key or not _:
            raise click.BadParameter(f"expected KEY=VALUE, got {pair!r}")
        try:
            out[key] = json.loads(value)
        except json.JSONDecodeError:
            out[key] = value
    return out


if __name__ == "__main__":
    cli()
