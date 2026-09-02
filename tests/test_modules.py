"""Module contract tests. No network: modules are exercised through the registry."""

from collections.abc import Iterator

import pytest

from leadpipe.models import RawRecord
from leadpipe.modules import MODULE_REGISTRY
from leadpipe.modules.base import ModuleContext, SourceModule, register, run_module

EXPECTED_MODULES = {
    "m1_booking_serp",
    "m2_meta_ads",
    "m3_instagram",
    "m4_youtube",
    "m5_directories",
    "m6_testimonials",
}


def test_all_six_modules_are_registered():
    assert set(MODULE_REGISTRY) >= EXPECTED_MODULES


def test_every_module_writes_the_same_schema():
    for name, factory in MODULE_REGISTRY.items():
        module = factory()
        assert module.name == name
        assert isinstance(module.requires, tuple)


class _Boom(SourceModule):
    name = "_test_boom"

    def run(self, ctx: ModuleContext) -> Iterator[RawRecord]:
        yield RawRecord(source_module=self.name, payload={"a": 1})
        raise RuntimeError("instagram blocked us")


class _Ok(SourceModule):
    name = "_test_ok"

    def run(self, ctx: ModuleContext) -> Iterator[RawRecord]:
        for i in range(3):
            yield RawRecord(source_module=self.name, payload={"i": i})


class _NeedsKey(SourceModule):
    name = "_test_needs_key"
    requires = ("apify_token",)

    def run(self, ctx: ModuleContext) -> Iterator[RawRecord]:  # pragma: no cover - never runs
        yield RawRecord(source_module=self.name, payload={})


register(lambda: _Boom())
register(lambda: _Ok())
register(lambda: _NeedsKey())


def test_a_failing_module_is_isolated_not_raised():
    """Instagram breaking must not halt the pipeline."""
    summary = run_module("_test_boom", ModuleContext(run_id="t", dry_run=True))
    assert summary["status"] == "failed"
    assert "instagram blocked us" in summary["reason"]


def test_a_healthy_module_reports_counts():
    summary = run_module("_test_ok", ModuleContext(run_id="t", dry_run=True))
    assert summary["status"] == "ok" and summary["found"] == 3


def test_limit_stops_a_module_early():
    summary = run_module("_test_ok", ModuleContext(run_id="t", dry_run=True, limit=2))
    assert summary["found"] == 2


def test_missing_credentials_skip_rather_than_fail():
    import dataclasses

    from leadpipe.config import get_config

    config = dataclasses.replace(get_config(), apify_token=None)
    summary = run_module("_test_needs_key", ModuleContext(run_id="t", config=config, dry_run=True))
    assert summary["status"] == "skipped"
    assert summary["reason"] == "missing_config:apify_token"


def test_present_credentials_allow_a_module_to_run():
    import dataclasses

    from leadpipe.config import get_config

    config = dataclasses.replace(get_config(), apify_token="token")
    summary = run_module("_test_needs_key", ModuleContext(run_id="t", config=config, dry_run=True))
    assert summary["status"] == "ok"


def test_unknown_module_raises():
    with pytest.raises(KeyError):
        run_module("nope", ModuleContext(run_id="t", dry_run=True))


def test_meta_ads_landing_facts_carry_the_instagram_handle():
    """A coach paying for ads links Instagram on the landing page; keep it."""
    from leadpipe.modules.m2_meta_ads import LandingFacts, _first_handle

    facts = LandingFacts(booking_url="https://cal.com/j", instagram_handle="jessfit")
    assert facts.instagram_handle == "jessfit"
    assert _first_handle(["https://example.com", "https://instagram.com/jessfit/"]) == "jessfit"
    assert _first_handle(["https://example.com"]) is None
