"""Source module contract and the isolated runner.

Every module is independent and writes to `raw_records` only. A module that
throws is logged as failed and the pipeline moves on - Instagram breaking must
never take the rest of the run down with it.
"""

from __future__ import annotations

import logging
import traceback
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from .. import db
from ..config import Config, get_config
from ..models import RawRecord

log = logging.getLogger(__name__)


@dataclass
class ModuleContext:
    run_id: str
    config: Config = field(default_factory=get_config)
    limit: int | None = None
    dry_run: bool = False
    # Extra per-run knobs, e.g. {"queries": [...], "seed_handles": [...]}
    options: dict = field(default_factory=dict)


class SourceModule(ABC):
    """One source. `name` is what lands in `source_modules` on the record."""

    name: str = "unnamed"
    #: Modules with a hard credential requirement declare it so runs fail loudly.
    requires: tuple[str, ...] = ()

    @abstractmethod
    def run(self, ctx: ModuleContext) -> Iterator[RawRecord]:
        """Yield raw observations. Never write to `coaches` from here."""

    def preflight(self, ctx: ModuleContext) -> str | None:
        """Return a reason string if the module cannot run."""
        for requirement in self.requires:
            if not getattr(ctx.config, requirement, None):
                return f"missing_config:{requirement}"
        return None


MODULE_REGISTRY: dict[str, Callable[[], SourceModule]] = {}


def register(factory: Callable[[], SourceModule]) -> Callable[[], SourceModule]:
    instance = factory()
    MODULE_REGISTRY[instance.name] = factory
    return factory


def get_module(name: str) -> SourceModule:
    if name not in MODULE_REGISTRY:
        known = ", ".join(sorted(MODULE_REGISTRY))
        raise KeyError(f"unknown module {name!r}. Known modules: {known}")
    return MODULE_REGISTRY[name]()


def run_module(name: str, ctx: ModuleContext, batch_size: int = 200) -> dict:
    """Run one module end to end, streaming its output into staging.

    Returns a summary dict. Never raises for a module-level failure; the error
    is recorded on `module_runs` so a weekly run reports rather than aborts.
    """
    module = get_module(name)
    blocked = module.preflight(ctx)
    if blocked:
        log.warning("module %s skipped: %s", name, blocked)
        return {"module": name, "status": "skipped", "reason": blocked, "found": 0, "new": 0}

    run_log_id = None if ctx.dry_run else db.start_module_run(ctx.run_id, name)
    found = 0
    new = 0
    buffer: list[RawRecord] = []

    try:
        for record in module.run(ctx):
            found += 1
            buffer.append(record)
            if len(buffer) >= batch_size:
                new += _flush(buffer, ctx)
                buffer = []
            if ctx.limit and found >= ctx.limit:
                break
        new += _flush(buffer, ctx)
    except Exception as exc:  # module failures are isolated by design
        error = f"{type(exc).__name__}: {exc}"
        log.error("module %s failed: %s\n%s", name, error, traceback.format_exc())
        if run_log_id:
            db.finish_module_run(run_log_id, "failed", found, new, error[:2000])
        return {"module": name, "status": "failed", "reason": error, "found": found, "new": new}

    if run_log_id:
        db.finish_module_run(run_log_id, "ok", found, new)
    log.info("module %s: %d observations, %d new", name, found, new)
    return {"module": name, "status": "ok", "found": found, "new": new}


def _flush(buffer: list[RawRecord], ctx: ModuleContext) -> int:
    if not buffer:
        return 0
    if ctx.dry_run:
        for record in buffer:
            log.info("[dry-run] %s %s", record.source_module, record.source_url)
        return len(buffer)
    return db.insert_raw(buffer, ctx.run_id)
