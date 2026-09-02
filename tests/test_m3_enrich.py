"""Module 3 enrichment-only mode: the cheap path to follower counts."""

import dataclasses

from leadpipe.config import get_config
from leadpipe.modules.base import ModuleContext
from leadpipe.modules.m3_instagram import InstagramModule


def ctx(**options) -> ModuleContext:
    config = dataclasses.replace(get_config(), apify_token="t", min_followers=1000, max_followers=150_000)
    return ModuleContext(run_id="t", config=config, dry_run=True, options=options)


def profile(handle: str, followers: int | None) -> dict:
    return {"username": handle, "followersCount": followers, "biography": "online coach"}


def build(module: InstagramModule, context: ModuleContext, prof: dict, enrich_only: bool):
    module._budget = None
    return module._build_record(context, prof, web=None, enrich_only=enrich_only)


def test_discovery_drops_out_of_band_profiles():
    """In discovery an out-of-band account is not worth a profile fetch."""
    module = InstagramModule()
    assert build(module, ctx(), profile("toobig", 400_000), enrich_only=False) is None
    assert build(module, ctx(), profile("toosmall", 200), enrich_only=False) is None
    assert build(module, ctx(), profile("justright", 12_000), enrich_only=False) is not None


def test_enrichment_keeps_out_of_band_profiles_so_they_can_be_rejected():
    """A 400K account already in the DB must come back and be disqualified,
    not silently dropped and left un-rejected forever."""
    module = InstagramModule()
    record = build(module, ctx(), profile("toobig", 400_000), enrich_only=True)
    assert record is not None
    assert record.payload["instagram_followers"] == 400_000

    from leadpipe.filters import disqualify

    assert disqualify(text="online coach", instagram_followers=400_000) == "follower_count_above_150000"


def test_enrichment_keeps_small_accounts():
    """The 1K floor is a discovery filter, not a global disqualifier."""
    module = InstagramModule()
    record = build(module, ctx(), profile("small", 200), enrich_only=True)
    assert record is not None and record.payload["instagram_followers"] == 200


def test_enrichment_skips_link_following_by_default():
    module = InstagramModule()
    prof = {**profile("j", 12_000), "externalUrl": "https://linktr.ee/j"}
    record = build(module, ctx(), prof, enrich_only=True)
    # No web client was passed; not following links is what keeps this safe.
    assert record.payload["booking_url"] is None


def test_explicit_handles_drive_the_enrichment_work_list():
    module = InstagramModule()
    handles = module._handles_to_enrich(ctx(enrich_only=True, handles=["@JessFit", "https://instagram.com/mike/"]))
    assert handles == ["jessfit", "mike"]


def test_a_dry_run_without_handles_does_nothing_rather_than_hitting_the_db():
    module = InstagramModule()
    assert module._handles_to_enrich(ctx(enrich_only=True)) == []


def test_result_budget_blocks_further_actor_calls():
    module = InstagramModule()
    module._budget = 10
    module._results_used = 10
    assert module._run_actor(ctx(), api=None, actor="a", payload={"usernames": ["x"]}) == []


def test_result_budget_allows_calls_within_the_ceiling():
    """Guards the budget check itself: under the cap it must not short-circuit."""
    module = InstagramModule()
    module._budget = 10
    module._results_used = 0

    class FailingApi:
        def post(self, *a, **k):
            raise AssertionError("reached the network, so the budget did not block")

    try:
        module._run_actor(ctx(), api=FailingApi(), actor="a", payload={"usernames": ["x"]})
    except AssertionError as exc:
        assert "did not block" in str(exc)
    else:
        raise AssertionError("expected the call to proceed past the budget check")
