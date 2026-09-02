"""SERP provider resolution, budget, and free-provider parsing."""

import dataclasses

import pytest

from leadpipe.config import get_config
from leadpipe.serp import SerpBudget, SerpClient, _unwrap_ddg


def client_with(**overrides) -> SerpClient:
    config = dataclasses.replace(get_config(), **overrides)
    import leadpipe.serp as serp_module

    original = serp_module.get_config
    serp_module.get_config = lambda: config
    try:
        return SerpClient(budget=SerpBudget())
    finally:
        serp_module.get_config = original


NO_KEYS = {
    "serper_api_key": None,
    "serpapi_key": None,
    "scraperapi_key": None,
    "brave_api_key": None,
}


def test_auto_falls_back_to_a_keyless_provider():
    """Zero setup must still run something."""
    client = client_with(serp_provider="auto", **NO_KEYS)
    assert client.provider == "duckduckgo"
    assert client.available()
    assert client.is_free


def test_auto_upgrades_when_a_key_appears():
    assert client_with(serp_provider="auto", **{**NO_KEYS, "brave_api_key": "k"}).provider == "brave"
    assert client_with(serp_provider="auto", **{**NO_KEYS, "serper_api_key": "k"}).provider == "serper"


def test_auto_prefers_paid_quality_when_both_are_configured():
    client = client_with(serp_provider="auto", **{**NO_KEYS, "brave_api_key": "b", "serper_api_key": "s"})
    assert client.provider == "serper"


def test_an_explicit_provider_is_never_overridden():
    client = client_with(serp_provider="duckduckgo", **{**NO_KEYS, "serper_api_key": "s"})
    assert client.provider == "duckduckgo"


def test_a_keyed_provider_without_its_key_is_unavailable():
    assert not client_with(serp_provider="brave", **NO_KEYS).available()


def test_free_providers_are_flagged_as_free():
    assert client_with(serp_provider="brave", **NO_KEYS).is_free
    assert not client_with(serp_provider="serper", **NO_KEYS).is_free


# -- budget ----------------------------------------------------------------


def test_budget_stops_at_the_limit():
    budget = SerpBudget(3)
    assert [budget.take() for _ in range(5)] == [True, True, True, False, False]
    assert budget.used == 3
    assert budget.remaining == 0


def test_budget_of_none_is_unlimited():
    budget = SerpBudget(None)
    assert all(budget.take() for _ in range(1000))
    assert budget.remaining is None


def test_budget_is_shared_so_run_all_cannot_overspend():
    """Modules 1, 5 and 6 draw on one allowance, not one each."""
    budget = SerpBudget(2)
    m1 = SerpClient(budget=budget)
    m5 = SerpClient(budget=budget)
    assert m1.budget.take() and m5.budget.take()
    assert not m1.budget.take()


def test_budget_reset():
    budget = SerpBudget(1)
    budget.take()
    budget.reset()
    assert budget.take()


def test_search_stops_once_the_budget_is_spent():
    budget = SerpBudget(0)
    client = client_with(serp_provider="duckduckgo", **NO_KEYS)
    client.budget = budget
    assert client.search("anything", pages=5) == []


# -- duckduckgo link unwrapping --------------------------------------------


@pytest.mark.parametrize(
    "href,expected",
    [
        (
            "//duckduckgo.com/l/?uddg=https%3A%2F%2Fcalendly.com%2Fjess%2F30min&rut=abc",
            "https://calendly.com/jess/30min",
        ),
        ("https://calendly.com/jess/30min", "https://calendly.com/jess/30min"),
        ("/settings", None),
        (None, None),
    ],
)
def test_unwrap_ddg(href, expected):
    assert _unwrap_ddg(href) == expected
