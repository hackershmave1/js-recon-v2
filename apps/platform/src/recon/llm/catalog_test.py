"""OpenRouter catalog: filter to models our threat-model call can use; cache with TTLs."""

import asyncio
from decimal import Decimal

import httpx
import pytest

from recon.llm import catalog, presets

_R = ["max_tokens", "response_format", "tools"]
SAMPLE = {
    "data": [
        {
            "id": "anthropic/claude-haiku-4.5",
            "name": "Anthropic: Claude Haiku 4.5",
            "context_length": 200000,
            "pricing": {"prompt": "0.000001", "completion": "0.000005"},
            "top_provider": {"max_completion_tokens": 64000},
            "supported_parameters": _R,
        },
        {
            "id": "anthropic/claude-sonnet-4.6",
            "name": "Anthropic: Claude Sonnet 4.6",
            "context_length": 1000000,
            "pricing": {"prompt": "0.000003", "completion": "0.000015"},
            "top_provider": {"max_completion_tokens": 128000},
            "supported_parameters": _R,
        },
        {
            "id": "anthropic/claude-opus-5.5",
            "name": "Anthropic: Claude Opus 5.5",
            "context_length": 1000000,
            "pricing": {"prompt": "0.000004", "completion": "0.00002"},
            "top_provider": {"max_completion_tokens": 128000},
            "supported_parameters": _R,
        },
        {
            "id": "anthropic/claude-sonnet-4.6:batch",
            "name": "Anthropic: Claude Sonnet 4.6 (batch)",
            "context_length": 1000000,
            "pricing": {"prompt": "0.0000015", "completion": "0.0000075"},
            "top_provider": {"max_completion_tokens": 128000},
            "supported_parameters": _R,
        },
        {
            "id": "apodex/apodex-1.1-mini:free",
            "name": "Apodex: Apodex 1.1 Mini (free)",
            "context_length": 262144,
            "pricing": {"prompt": "0", "completion": "0"},
            "top_provider": {"max_completion_tokens": 235929},
            "supported_parameters": _R,
        },
        {
            "id": "typesafe/jev-router",
            "name": "TypeSafe: Jev Router",
            "context_length": 1000000,
            "pricing": {"prompt": "-1", "completion": "-1"},
            "top_provider": {"max_completion_tokens": None},
            "supported_parameters": _R,
        },
        {
            "id": "inference-net/schematron-v2-turbo",
            "name": "Inference.net: Schematron V2 Turbo",
            "context_length": 128000,
            "pricing": {"prompt": "0.00000003", "completion": "0.00000015"},
            "top_provider": {"max_completion_tokens": 8192},
            "supported_parameters": ["max_tokens", "response_format"],
        },
        {
            "id": "inclusionai/ling-3.1-flash",
            "name": "inclusionAI: Ling 3.1 Flash",
            "context_length": 262144,
            "pricing": {"prompt": "0", "completion": "0"},
            "top_provider": {"max_completion_tokens": 32768},
            "supported_parameters": ["max_tokens", "tools"],
        },
    ]
}
KEPT = [
    "anthropic/claude-haiku-4.5",
    "anthropic/claude-opus-5.5",
    "anthropic/claude-sonnet-4.6",
    "apodex/apodex-1.1-mini:free",
]


@pytest.fixture(autouse=True)
def _fresh_cache():
    catalog.reset()
    yield
    catalog.reset()


def _factory(handler):
    return lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _counting_ok(calls):
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=SAMPLE)

    return handler


def _down(request):
    raise httpx.ConnectError("unreachable", request=request)


def test_parse_keeps_only_models_our_call_can_use():
    # Dropped: :batch, "-1" variable price, 8192-token output cap, no response_format.
    assert [m.id for m in catalog.parse_models(SAMPLE)] == KEPT


def test_malformed_entries_are_skipped_individually():
    good = SAMPLE["data"][0]
    bad = [
        "not-a-dict",
        None,
        {**good, "id": "x/top-provider-list", "top_provider": []},
        {**good, "id": "x/pricing-list", "pricing": []},
        {**good, "id": "x/params-string", "supported_parameters": "response_format"},
    ]
    named = {**good, "id": "x/name-number", "name": 42}
    parsed = catalog.parse_models({"data": [*SAMPLE["data"], *bad, named]})
    assert [m.id for m in parsed] == sorted([*KEPT, "x/name-number"])
    assert all(isinstance(m.name, str) for m in parsed)
    assert {m.id: m.name for m in parsed}["x/name-number"] == "42"


def test_openrouter_builtin_presets_exist_in_the_recorded_catalog():
    ids = {m.id for m in catalog.parse_models(SAMPLE)}
    assert set(presets.BUILTIN_PRESET_MODELS["openrouter"].values()) <= ids


def test_prices_are_decimal_per_token_and_free_is_zero():
    by = {m.id: m for m in catalog.parse_models(SAMPLE)}
    assert by["anthropic/claude-sonnet-4.6"].prompt_price == Decimal("0.000003")
    assert by["apodex/apodex-1.1-mini:free"].completion_price == 0


def test_fetch_once_then_serve_from_cache():
    calls: list[str] = []
    snap = asyncio.run(catalog.get_catalog(_factory(_counting_ok(calls))))
    assert snap["available"] is True and snap["stale"] is False
    assert [m["id"] for m in snap["models"]] == KEPT
    assert snap["models"][0]["prompt_price"] == "0.000001"
    asyncio.run(catalog.get_catalog(_factory(_counting_ok(calls))))
    assert calls == [catalog.CATALOG_URL]
    assert catalog.cached_ids() == frozenset(KEPT)


def test_cache_expires_after_an_hour(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(catalog, "_now", lambda: now[0])
    calls: list[str] = []
    asyncio.run(catalog.get_catalog(_factory(_counting_ok(calls))))
    now[0] += 3601
    asyncio.run(catalog.get_catalog(_factory(_counting_ok(calls))))
    assert len(calls) == 2


def test_failure_without_cache_is_unavailable_and_not_retried_for_5_minutes(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(catalog, "_now", lambda: now[0])
    snap = asyncio.run(catalog.get_catalog(_factory(_down)))
    assert snap == {"available": False, "stale": False, "fetched_at": None, "models": []}
    calls: list[str] = []
    now[0] += 299
    asyncio.run(catalog.get_catalog(_factory(_counting_ok(calls))))
    assert calls == []
    now[0] += 2
    assert asyncio.run(catalog.get_catalog(_factory(_counting_ok(calls))))["available"] is True


def test_failure_with_cache_serves_the_stale_copy(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(catalog, "_now", lambda: now[0])
    asyncio.run(catalog.get_catalog(_factory(_counting_ok([]))))
    now[0] += 3601
    snap = asyncio.run(catalog.get_catalog(_factory(_down)))
    assert snap["available"] is True and snap["stale"] is True
    assert len(snap["models"]) == len(KEPT)


def test_cached_ids_is_none_before_any_fetch():
    assert catalog.cached_ids() is None


def _malformed(request):
    return httpx.Response(200, json=["not", "a", "dict"])


def test_malformed_body_with_cache_serves_the_stale_copy(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(catalog, "_now", lambda: now[0])
    asyncio.run(catalog.get_catalog(_factory(_counting_ok([]))))
    now[0] += 3601
    snap = asyncio.run(catalog.get_catalog(_factory(_malformed)))
    assert snap["available"] is True and snap["stale"] is True
    assert len(snap["models"]) == len(KEPT)


def test_malformed_body_without_cache_is_unavailable_and_negatively_cached(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(catalog, "_now", lambda: now[0])
    snap = asyncio.run(catalog.get_catalog(_factory(_malformed)))
    assert snap == {"available": False, "stale": False, "fetched_at": None, "models": []}
    calls: list[str] = []
    now[0] += 299
    asyncio.run(catalog.get_catalog(_factory(_counting_ok(calls))))
    assert calls == []
