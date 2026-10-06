# LLM Model Catalog + Presets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let teams pick threat-model LLMs from OpenRouter's live catalog with per-run cost estimates, and choose Cheapest / Balanced / Strongest presets (admin-overridable) per team and per run.

**Architecture:** Pure `llm/presets.py` resolves (preset, credential provider) → model. `llm/catalog.py` fetches and caches OpenRouter's public catalog. `llm/cost.py` averages the team's real token history. Migration 0030 adds `tenant_llm_config.preset_models`. `/settings/llm` gains preset views and a `/models` sub-route. Threat-model generation takes an optional preset, claims runs atomically, and exposes `/threat-model/presets`. Web: a shared `src/api/llmCatalog.ts`, a ModelPicker plus PresetRows in Settings, and a preset select on the Threat Model tab.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, Postgres RLS, httpx (`AsyncClient`, `MockTransport` in tests), React + vitest/Testing Library.

**Spec:** `apps/platform/docs/superpowers/specs/2026-10-07-llm-model-presets-design.md`

## Global Constraints

- Branch `feat/llm-model-presets`, working tree `C:\Users\omri\Documents\claude-sessions\js-extractor-v2`. Backend commands from `apps/platform`; web from `apps/platform/web`.
- **Invariant:** a key always travels with its own provider. A preset never changes the provider. Admin overrides apply only when `credential_provider == team_provider`.
- Presets are exactly `("cheapest", "balanced", "strongest")`. `balanced == DEFAULT_MODELS[provider]`.
- **OpenRouter model IDs are catalog IDs, with dots** (`anthropic/claude-sonnet-4.6`). Direct Anthropic IDs use hyphens (`claude-sonnet-4-6`).
- Threat models send `max_tokens=12000`. Catalog keeps models with `response_format` support, `context_length >= 32000`, and `top_provider.max_completion_tokens` either unset or `>= 12000`. It drops `:batch` IDs and negative or unparseable `pricing.prompt`/`pricing.completion`. `"0"` is kept (free).
- Catalog cache: 1 h after a good fetch, 5 min after a failed one, in-process. Only `GET /settings/llm/models` may fetch; `GET /settings/llm` reads the cache only.
- Cost estimate: average `prompt_tokens`/`completion_tokens` of this tenant's `session_threat_model` rows with `status='done' AND prompt_tokens > 0`. With none: assumed 20000 / 4000, `basis: "assumed"`.
- `PUT /settings/llm` `preset_models`: omitted → keep stored; `null` or `{}` → clear; map → replace. A provider change with it omitted clears it.
- Every tenant-table DB access goes through `tenant_session(tenant_id)` with `flush()`, never `commit()`. Never log, return or render an API key.
- Integration tests run ONLY in an isolated compose project (recipe below), never against the user's live `platform` stack.
- Web: features never import from each other (CLAUDE.md §9). Shared catalog types and helpers live in `web/src/api/llmCatalog.ts`. `apiClient.ts` (over the line cap) only gains the optional `preset` arg on `triggerThreatModel`.
- Ruff `F,I,UP,B,C4,SIM,PIE,RET` + `ruff format`; mypy strict on findings/spec only; files ≤ ~300 lines; comments explain why.
- Commits: Conventional Commits, multi-line, ending with a `Co-Authored-By:` trailer naming the model that actually wrote them.

### Running integration tests

From `apps/platform`, with `$TEMP/isolated-override.yml`:

```yaml
services:
  postgres: { ports: !reset [] }
  redis: { ports: !reset [] }
  minio: { ports: !reset [] }
  api: { ports: !reset [], image: "recon-platform:plan" }
  migrate: { image: "recon-platform:plan" }
  worker: { image: "recon-platform:plan" }
```

```bash
F="-p recon-plan -f docker-compose.yml -f $TEMP/isolated-override.yml"
docker compose $F up -d --build postgres redis minio migrate fixture-site
MSYS_NO_PATHCONV=1 docker compose $F run --rm --user root -e RECON_AUTH_SECRET="" api sh -c "pip install --quiet pytest fakeredis && pytest -p no:cacheprovider -o addopts='' -q <TEST PATHS>"
docker compose $F down -v   # when finished
```

The image bakes the source, so re-run `up -d --build` after each code change. Host-lane tests (no `integration` marker): `RECON_REQUIRE_ENGINES=1 uv run pytest -m "not integration" -q <paths>`.

---

### Task 1: Presets module, catalog-correct OpenRouter defaults, `require_parameters`

**Files:**
- Create: `apps/platform/src/recon/llm/presets.py`
- Create: `apps/platform/src/recon/llm/presets_test.py`
- Create: `apps/platform/src/recon/llm/provider_test.py`
- Modify: `apps/platform/src/recon/llm/provider.py` (`DEFAULT_MODELS["openrouter"]`, docstring example line 9, `OpenRouterProvider.generate_structured` create call)
- Modify: `apps/capture/chrome-extension/src/popup/components/SettingsView.jsx` (`LLM_MODELS.openrouter` hyphenated IDs)

**Interfaces:**
- Produces: `presets.PRESETS: tuple[str, ...]`, `presets.BUILTIN_PRESET_MODELS: dict[str, dict[str, str]]`, `presets.resolve_model(preset: str | None, credential_provider: str, team_provider: str | None, overrides: Mapping[str, str], fallback_model: str | None) -> str | None`.

- [ ] **Step 1: Write the failing tests.** `llm/presets_test.py` (host lane, no marker):

```python
"""Presets resolve to a model for the credential's own provider, never another's."""

from recon.llm import presets
from recon.llm.presets import resolve_model
from recon.llm.provider import DEFAULT_MODELS, VALID_PROVIDERS


def test_every_provider_has_all_three_presets_and_balanced_is_the_default():
    assert set(presets.BUILTIN_PRESET_MODELS) == set(VALID_PROVIDERS)
    for provider, table in presets.BUILTIN_PRESET_MODELS.items():
        assert set(table) == set(presets.PRESETS)
        assert table["balanced"] == DEFAULT_MODELS[provider]


def test_openrouter_default_is_a_catalog_id():
    # OpenRouter's catalog spells versions with dots; "anthropic/claude-sonnet-4-6" isn't listed.
    assert DEFAULT_MODELS["openrouter"] == "anthropic/claude-sonnet-4.6"


def test_no_preset_keeps_the_credentials_own_model():
    assert resolve_model(None, "anthropic", "anthropic", {"cheapest": "x"}, "fb") == "fb"
    assert resolve_model(None, "openrouter", None, {}, None) is None


def test_builtin_used_without_an_override():
    assert resolve_model("strongest", "anthropic", None, {}, None) == "claude-opus-5-5"
    assert resolve_model("balanced", "gemini", "openrouter", {}, None) == "gemini-2.5-flash"


def test_team_override_applies_only_to_the_team_provider():
    overrides = {"balanced": "vendor/team-pick"}
    assert resolve_model("balanced", "openrouter", "openrouter", overrides, None) == "vendor/team-pick"
    # A session's own Anthropic key must never be paired with an OpenRouter model id.
    assert resolve_model("balanced", "anthropic", "openrouter", overrides, None) == "claude-sonnet-4-6"


def test_cheapest_on_openrouter_routes_to_the_cheapest_host():
    assert resolve_model("cheapest", "openrouter", None, {}, None) == "anthropic/claude-haiku-4.5:floor"
    # An explicit variant is kept as-is, and direct providers never get one.
    over = {"cheapest": "vendor/model:free"}
    assert resolve_model("cheapest", "openrouter", "openrouter", over, None) == "vendor/model:free"
    assert resolve_model("cheapest", "anthropic", None, {}, None) == "claude-haiku-4-5-20251001"
```

`llm/provider_test.py` (host lane):

```python
"""OpenRouter routing must only use hosts that honour response_format."""

import asyncio
from types import SimpleNamespace

import openai
from pydantic import BaseModel

from recon.llm.provider import OpenRouterProvider


class _Out(BaseModel):
    ok: bool


def test_openrouter_requires_hosts_that_support_our_parameters(monkeypatch):
    captured: dict = {}

    class _Completions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            message = SimpleNamespace(content='{"ok": true}')
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message, finish_reason="stop")],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
            )

    class _Client:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=_Completions())

    monkeypatch.setattr(openai, "AsyncOpenAI", _Client)
    asyncio.run(OpenRouterProvider(api_key="k", model="m").generate_structured("s", "u", _Out, 10))
    assert captured["extra_body"] == {"provider": {"require_parameters": True}}
    assert captured["response_format"] == {"type": "json_object"}
```

- [ ] **Step 2: Run to verify they fail.** `RECON_REQUIRE_ENGINES=1 uv run pytest -m "not integration" -q src/recon/llm/presets_test.py src/recon/llm/provider_test.py`. Expected: `ModuleNotFoundError: recon.llm.presets`, and a `KeyError: 'extra_body'`. If the fake usage object doesn't match what `OpenRouterProvider` reads, adjust only the fake so the call completes; the assertions stay.

- [ ] **Step 3: Implement.** `llm/presets.py`:

```python
"""Cost/strength presets: Cheapest · Balanced · Strongest → a model for the credential's
own provider. A preset never changes the provider (the key must travel with its provider),
so team overrides, which are model ids for the team provider, apply only to that provider."""

from __future__ import annotations

from collections.abc import Mapping

from recon.llm.provider import DEFAULT_MODELS

PRESETS: tuple[str, ...] = ("cheapest", "balanced", "strongest")

# NOTE: OpenRouter ids are catalog ids (dots); direct Anthropic ids use hyphens. "balanced"
# is each provider's DEFAULT_MODELS entry so today's default stays the middle option.
BUILTIN_PRESET_MODELS: dict[str, dict[str, str]] = {
    "anthropic": {
        "cheapest": "claude-haiku-4-5-20251001",
        "balanced": DEFAULT_MODELS["anthropic"],
        "strongest": "claude-opus-5-5",
    },
    "openrouter": {
        "cheapest": "anthropic/claude-haiku-4.5",
        "balanced": DEFAULT_MODELS["openrouter"],
        "strongest": "anthropic/claude-opus-5.5",
    },
    "gemini": {
        "cheapest": "gemini-2.5-flash-lite",
        "balanced": DEFAULT_MODELS["gemini"],
        "strongest": "gemini-2.5-pro",
    },
}


def resolve_model(
    preset: str | None,
    credential_provider: str,
    team_provider: str | None,
    overrides: Mapping[str, str],
    fallback_model: str | None,
) -> str | None:
    """The model to run. None preset keeps the credential's own model (may be None: the
    provider default). Cheapest on OpenRouter gets ``:floor`` (cheapest host, same model)."""
    if preset is None:
        return fallback_model
    model = overrides.get(preset) if credential_provider == team_provider else None
    if not model:
        model = BUILTIN_PRESET_MODELS[credential_provider][preset]
    if credential_provider == "openrouter" and preset == "cheapest" and ":" not in model:
        model += ":floor"
    return model
```

In `llm/provider.py`: set `"openrouter": "anthropic/claude-sonnet-4.6",` in `DEFAULT_MODELS` and update the docstring example on line 9 to the same ID. In `OpenRouterProvider.generate_structured`, add to the `client.chat.completions.create(...)` call, right after `response_format=...`:

```python
            # Without this, OpenRouter may route (incl. :floor's price sort) to a host that
            # ignores response_format and returns prose, which we can't parse.
            extra_body={"provider": {"require_parameters": True}},
```

In `apps/capture/chrome-extension/src/popup/components/SettingsView.jsx`, under `LLM_MODELS.openrouter`, change `'anthropic/claude-sonnet-4-6'` → `'anthropic/claude-sonnet-4.6'` and `'anthropic/claude-opus-4-7'` → `'anthropic/claude-opus-4.7'` (catalog IDs; leave the direct `anthropic:` list alone).

- [ ] **Step 4: Run to verify they pass.** Same pytest command, then `uv run ruff check src && uv run ruff format --check src`. From `apps/capture/chrome-extension`: `npm run build` then `for t in tests/test_*.mjs; do node "$t" || echo FAIL $t; done`, then `git checkout -- dist`.

- [ ] **Step 5: Commit**

```bash
git add apps/platform/src/recon/llm/presets.py apps/platform/src/recon/llm/presets_test.py apps/platform/src/recon/llm/provider.py apps/platform/src/recon/llm/provider_test.py apps/capture/chrome-extension/src/popup/components/SettingsView.jsx
git commit -m "feat(llm): cost/strength presets and catalog-correct OpenRouter model ids" -m "presets.py maps Cheapest/Balanced/Strongest to a model for the credential's own provider; a team override applies only when the credential is from the team provider, so a key is never paired with another provider's model id. Cheapest on OpenRouter adds :floor (cheapest host, same model).

OpenRouter's catalog spells versions with dots, so DEFAULT_MODELS['openrouter'] and the extension's OpenRouter list move from anthropic/claude-sonnet-4-6 / -opus-4-7 to the listed ids. OpenRouterProvider now sets provider.require_parameters so routing only picks hosts that honour response_format.

Co-Authored-By: <the model you are>"
```

---

### Task 2: OpenRouter catalog with cache

**Files:**
- Create: `apps/platform/src/recon/llm/catalog.py`
- Create: `apps/platform/src/recon/llm/catalog_test.py`

**Interfaces:**
- Consumes: `presets.BUILTIN_PRESET_MODELS` (Task 1), in a test.
- Produces: `catalog.SAMPLE`-shaped payload knowledge; `catalog.parse_models(payload: dict) -> list[CatalogModel]`; `async catalog.get_catalog(client_factory: Callable[[], httpx.AsyncClient] | None = None) -> dict` returning `{available: bool, stale: bool, fetched_at: str | None, models: list[dict]}`, each model `{id, name, context_length, max_completion_tokens, prompt_price: str, completion_price: str}`; `catalog.cached_ids() -> frozenset[str] | None`; `catalog.reset() -> None`; module function `catalog._client() -> httpx.AsyncClient` (patchable in tests); `catalog._now() -> float`.

- [ ] **Step 1: Write the failing tests.** `llm/catalog_test.py` (host lane). `SAMPLE` is trimmed from the live catalog fetched 2026-10-07 (real IDs and prices):

```python
"""OpenRouter catalog: filter to models our threat-model call can use; cache with TTLs."""

import asyncio
from decimal import Decimal

import httpx
import pytest

from recon.llm import catalog, presets

_R = ["max_tokens", "response_format", "tools"]
SAMPLE = {
    "data": [
        {"id": "anthropic/claude-haiku-4.5", "name": "Anthropic: Claude Haiku 4.5", "context_length": 200000,
         "pricing": {"prompt": "0.000001", "completion": "0.000005"},
         "top_provider": {"max_completion_tokens": 64000}, "supported_parameters": _R},
        {"id": "anthropic/claude-sonnet-4.6", "name": "Anthropic: Claude Sonnet 4.6", "context_length": 1000000,
         "pricing": {"prompt": "0.000003", "completion": "0.000015"},
         "top_provider": {"max_completion_tokens": 128000}, "supported_parameters": _R},
        {"id": "anthropic/claude-opus-5.5", "name": "Anthropic: Claude Opus 5.5", "context_length": 1000000,
         "pricing": {"prompt": "0.000004", "completion": "0.00002"},
         "top_provider": {"max_completion_tokens": 128000}, "supported_parameters": _R},
        {"id": "anthropic/claude-sonnet-4.6:batch", "name": "Anthropic: Claude Sonnet 4.6 (batch)",
         "context_length": 1000000, "pricing": {"prompt": "0.0000015", "completion": "0.0000075"},
         "top_provider": {"max_completion_tokens": 128000}, "supported_parameters": _R},
        {"id": "apodex/apodex-1.1-mini:free", "name": "Apodex: Apodex 1.1 Mini (free)", "context_length": 262144,
         "pricing": {"prompt": "0", "completion": "0"},
         "top_provider": {"max_completion_tokens": 235929}, "supported_parameters": _R},
        {"id": "typesafe/jev-router", "name": "TypeSafe: Jev Router", "context_length": 1000000,
         "pricing": {"prompt": "-1", "completion": "-1"},
         "top_provider": {"max_completion_tokens": None}, "supported_parameters": _R},
        {"id": "inference-net/schematron-v2-turbo", "name": "Inference.net: Schematron V2 Turbo",
         "context_length": 128000, "pricing": {"prompt": "0.00000003", "completion": "0.00000015"},
         "top_provider": {"max_completion_tokens": 8192}, "supported_parameters": ["max_tokens", "response_format"]},
        {"id": "inclusionai/ling-3.1-flash", "name": "inclusionAI: Ling 3.1 Flash", "context_length": 262144,
         "pricing": {"prompt": "0", "completion": "0"},
         "top_provider": {"max_completion_tokens": 32768}, "supported_parameters": ["max_tokens", "tools"]},
    ]
}
KEPT = ["anthropic/claude-haiku-4.5", "anthropic/claude-opus-5.5", "anthropic/claude-sonnet-4.6",
        "apodex/apodex-1.1-mini:free"]


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
```

- [ ] **Step 2: Run to verify it fails.** `RECON_REQUIRE_ENGINES=1 uv run pytest -m "not integration" -q src/recon/llm/catalog_test.py` → `ImportError` (no `catalog`).

- [ ] **Step 3: Implement** `llm/catalog.py`:

```python
"""OpenRouter's public model catalog, filtered to models our threat-model call can use.

Cached in-process: the api runs a single uvicorn worker (docker-compose api command). A
failed fetch is cached too (5 min), so an unreachable OpenRouter doesn't stall every
request. Only GET /settings/llm/models fetches; other readers use cached_ids()."""

from __future__ import annotations

import asyncio
import datetime as dt
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from recon.observability import get_logger

log = get_logger("recon.llm.catalog")

CATALOG_URL = "https://openrouter.ai/api/v1/models"
_TTL_OK = 3600.0
_TTL_FAILED = 300.0
# Threat models send max_tokens=12000 and treat finish_reason="length" as a failure.
_MIN_OUTPUT_TOKENS = 12000
_MIN_CONTEXT = 32000


@dataclass(frozen=True)
class CatalogModel:
    id: str
    name: str
    context_length: int
    max_completion_tokens: int | None
    prompt_price: Decimal  # USD per token
    completion_price: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "context_length": self.context_length,
            "max_completion_tokens": self.max_completion_tokens,
            "prompt_price": str(self.prompt_price),
            "completion_price": str(self.completion_price),
        }


def _price(value: Any) -> Decimal | None:
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    # OpenRouter uses "-1" for variable-priced routers: no usable per-token price.
    return price if price.is_finite() and price >= 0 else None


def parse_models(payload: dict[str, Any]) -> list[CatalogModel]:
    kept: list[CatalogModel] = []
    for raw in payload.get("data") or []:
        model_id = raw.get("id")
        if not isinstance(model_id, str) or model_id.endswith(":batch"):
            continue
        if "response_format" not in (raw.get("supported_parameters") or []):
            continue
        context = raw.get("context_length")
        if not isinstance(context, int) or context < _MIN_CONTEXT:
            continue
        max_out = (raw.get("top_provider") or {}).get("max_completion_tokens")
        if isinstance(max_out, int) and max_out < _MIN_OUTPUT_TOKENS:
            continue
        pricing = raw.get("pricing") or {}
        prompt, completion = _price(pricing.get("prompt")), _price(pricing.get("completion"))
        if prompt is None or completion is None:
            continue
        kept.append(
            CatalogModel(
                id=model_id,
                name=raw.get("name") or model_id,
                context_length=context,
                max_completion_tokens=max_out if isinstance(max_out, int) else None,
                prompt_price=prompt,
                completion_price=completion,
            )
        )
    return sorted(kept, key=lambda m: m.id)


_state: dict[str, Any] = {}
_lock = asyncio.Lock()


def _now() -> float:
    return time.monotonic()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10.0)


def reset() -> None:
    """Clear the module cache (tests; the lock is recreated per event loop)."""
    global _lock
    _state.clear()
    _state.update(models=None, fetched_at=None, expires=0.0, stale=False)
    _lock = asyncio.Lock()


reset()


def _snapshot() -> dict[str, Any]:
    models = _state["models"]
    return {
        "available": models is not None,
        "stale": _state["stale"],
        "fetched_at": _state["fetched_at"],
        "models": [m.to_dict() for m in models or []],
    }


def cached_ids() -> frozenset[str] | None:
    models = _state["models"]
    return None if models is None else frozenset(m.id for m in models)


async def get_catalog(client_factory: Callable[[], httpx.AsyncClient] | None = None) -> dict[str, Any]:
    if _now() < _state["expires"]:
        return _snapshot()
    async with _lock:
        if _now() < _state["expires"]:  # another request refreshed it while we waited
            return _snapshot()
        started = _now()
        try:
            async with (client_factory or _client)() as client:
                response = await client.get(CATALOG_URL)
                response.raise_for_status()
                models = parse_models(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            _state["expires"] = _now() + _TTL_FAILED
            _state["stale"] = _state["models"] is not None
            log.warning(
                "llm.catalog.fetch_failed",
                error_type=type(exc).__name__,
                served_stale=_state["stale"],
            )
        else:
            _state.update(
                models=models,
                fetched_at=dt.datetime.now(dt.UTC).isoformat(),
                expires=_now() + _TTL_OK,
                stale=False,
            )
            log.info("llm.catalog.fetched", count=len(models), ms=round((_now() - started) * 1000))
    return _snapshot()
```

- [ ] **Step 4: Run to verify it passes.** Same command (all 8 pass), then ruff check + format check.

- [ ] **Step 5: Commit**

```bash
git add apps/platform/src/recon/llm/catalog.py apps/platform/src/recon/llm/catalog_test.py
git commit -m "feat(llm): cached OpenRouter model catalog filtered to usable models" -m "Fetches OpenRouter's public /api/v1/models and keeps only models the threat-model call can use: response_format support, at least 32k context, and an output cap of at least 12000 tokens (we send max_tokens=12000, and 'length' is a hard failure). It drops :batch duplicates and '-1' variable prices; free ('0') models are kept. Prices are parsed as per-token Decimals.

Cached in-process (single uvicorn worker): 1 h after a good fetch, 5 min after a failed one, with the last good copy served stale. An asyncio.Lock shares one fetch between concurrent requests. Logs llm.catalog.fetched/fetch_failed. Tests use a catalog sample recorded from the live API on 2026-10-07 and httpx.MockTransport; there is no network in tests.

Co-Authored-By: <the model you are>"
```

---

### Task 3: `preset_models` column (migration 0030) + tenant_config support

**Files:**
- Modify: `apps/platform/src/recon/db/models.py` (`TenantLlmConfig`: add `preset_models`)
- Create: `apps/platform/src/recon/migrations/versions/0030_llm_preset_models.py`
- Modify: `apps/platform/src/recon/llm/tenant_config.py`
- Modify: `apps/platform/src/recon/llm/tenant_config_test.py`

**Interfaces:**
- Produces: `TenantLlmConfig.preset_models: dict[str, str] | None` (JSONB). `tenant_config.KEEP_PRESETS` (sentinel). `tenant_config.save_config(tenant_id, user_id, provider, model, api_key, preset_models=KEEP_PRESETS)`. Serialized config gains `"preset_models": dict[str, str]` (`{}` when unset). `tenant_config.load_preset_context(tenant_id) -> tuple[str | None, dict[str, str]]` (no decrypt). `tenant_config.team_key_provider(tenant_id) -> str | None` (no decrypt).

- [ ] **Step 1: Write the failing tests.** Append to `llm/tenant_config_test.py` (it already has `_seed_user(role)` and `pytestmark = integration`):

```python
from cryptography.fernet import Fernet

from recon.config import get_settings
from recon.db import models
from recon.db.base import tenant_session
from recon.llm.crypto import KeyDecryptError


def test_preset_models_omitted_keeps_null_clears_map_replaces():
    tenant, admin = _seed_user("admin")
    tenant_config.save_config(tenant, admin, "openrouter", "m", "k", {"strongest": "vendor/big"})
    tenant_config.save_config(tenant, admin, "openrouter", "m2", "")  # omitted → keep
    assert tenant_config.get_config(tenant, include_actor=False)["preset_models"] == {"strongest": "vendor/big"}
    tenant_config.save_config(tenant, admin, "openrouter", "m2", "", {"cheapest": "vendor/small"})
    assert tenant_config.get_config(tenant, include_actor=False)["preset_models"] == {"cheapest": "vendor/small"}
    tenant_config.save_config(tenant, admin, "openrouter", "m2", "", None)  # null → clear
    assert tenant_config.get_config(tenant, include_actor=False)["preset_models"] == {}


def test_provider_change_clears_overrides_unless_new_ones_are_sent():
    tenant, admin = _seed_user("admin")
    tenant_config.save_config(tenant, admin, "openrouter", "m", "k", {"balanced": "vendor/x"})
    tenant_config.save_config(tenant, admin, "anthropic", "claude-x", "k2")
    assert tenant_config.get_config(tenant, include_actor=False)["preset_models"] == {}
    tenant_config.save_config(tenant, admin, "openrouter", "m", "k3", {"balanced": "vendor/y"})
    assert tenant_config.get_config(tenant, include_actor=False)["preset_models"] == {"balanced": "vendor/y"}


def test_preset_context_and_key_provider_never_decrypt(monkeypatch):
    tenant, admin = _seed_user("admin")
    tenant_config.save_config(tenant, admin, "openrouter", "m", "k", {"cheapest": "vendor/c"})
    monkeypatch.setenv("RECON_LLM_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    try:
        # The stored (cleartext-mode) key can't be decrypted under the new key.
        with pytest.raises(KeyDecryptError):
            tenant_config.load_key(tenant)
        assert tenant_config.load_preset_context(tenant) == ("openrouter", {"cheapest": "vendor/c"})
        assert tenant_config.team_key_provider(tenant) == "openrouter"
    finally:
        get_settings.cache_clear()


def test_preset_context_without_team_config():
    tenant, _ = _seed_user("admin")
    assert tenant_config.load_preset_context(tenant) == (None, {})
    assert tenant_config.team_key_provider(tenant) is None
```

(Add any of these imports only if the file doesn't already have them; `pytest` is already imported.)

- [ ] **Step 2: Run to verify it fails** (integration; paths `src/recon/llm/tenant_config_test.py`). Expected: `TypeError` (unexpected positional arg) / `AttributeError` (no `load_preset_context`).

- [ ] **Step 3: Implement.**

`db/models.py`, in `class TenantLlmConfig`, after `tested_at`:

```python
    # Admin overrides for the Cheapest/Balanced/Strongest presets: model ids for THIS
    # row's provider, so a provider change clears them (llm.tenant_config.save_config).
    preset_models: Mapped[dict[str, str] | None] = mapped_column(JSONB)
```

`migrations/versions/0030_llm_preset_models.py`:

```python
"""Add tenant_llm_config.preset_models: admin overrides for the model presets

Revision ID: 0030_llm_preset_models
Revises: 0029_tenant_llm_config
Create Date: 2026-10-07

(Revision id kept <=32 chars for Postgres' ``alembic_version`` column; it is 22.)
The table's existing RLS policy and grants cover new columns.
"""

from __future__ import annotations

from alembic import op

revision = "0030_llm_preset_models"
down_revision = "0029_tenant_llm_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS: on a fresh DB, 0001/0029's create_all already built the column from
    # the live model, so a bare add_column would crash with DuplicateColumn.
    op.execute("ALTER TABLE tenant_llm_config ADD COLUMN IF NOT EXISTS preset_models JSONB")


def downgrade() -> None:
    op.drop_column("tenant_llm_config", "preset_models")
```

`llm/tenant_config.py`: add `from collections.abc import Mapping` to the imports, and after `ProviderKeyRequired`:

```python
# Sentinel for "PUT omitted preset_models": keep what's stored (None means clear).
KEEP_PRESETS: Any = object()
```

Change the `save_config` signature and add the preset handling right after the `values` dict is built (before the `if api_key:` line):

```python
def save_config(
    tenant_id: str,
    user_id: str,
    provider: str,
    model: str,
    api_key: str,
    preset_models: Mapping[str, str] | None | Any = KEEP_PRESETS,
) -> dict[str, Any] | None:
```

```python
        if preset_models is KEEP_PRESETS:
            # Overrides are model ids for the stored provider; they can't follow a switch.
            if existing is not None and existing.provider != provider:
                values["preset_models"] = None
        else:
            values["preset_models"] = dict(preset_models) if preset_models else None
```

In `_serialize`, add `"preset_models": dict(row.preset_models or {}),` to the returned dict. Then add:

```python
def load_preset_context(tenant_id: str) -> tuple[str | None, dict[str, str]]:
    """Team provider + preset overrides, without touching the key: load_key decrypts and
    can raise, and preset resolution must not fail on a bad key."""
    with tenant_session(tenant_id) as db:
        row = db.execute(
            select(TenantLlmConfig.provider, TenantLlmConfig.preset_models).where(
                TenantLlmConfig.tenant_id == uuid.UUID(tenant_id)
            )
        ).first()
    if row is None:
        return None, {}
    return row.provider, dict(row.preset_models or {})


def team_key_provider(tenant_id: str) -> str | None:
    """Provider of the saved team key, if any — no decrypt."""
    with tenant_session(tenant_id) as db:
        return db.execute(
            select(TenantLlmConfig.provider).where(
                TenantLlmConfig.tenant_id == uuid.UUID(tenant_id),
                TenantLlmConfig.encrypted_api_key.is_not(None),
            )
        ).scalar_one_or_none()
```

- [ ] **Step 4: Run to verify it passes.** Paths `src/recon/llm/ src/recon/db/llm_threat_model_rls_test.py` (whole llm package, to prove nothing else broke). Check `docker compose $F logs migrate` shows no error. Lint.

- [ ] **Step 5: Commit**

```bash
git add apps/platform/src/recon/db/models.py apps/platform/src/recon/migrations/versions/0030_llm_preset_models.py apps/platform/src/recon/llm/tenant_config.py apps/platform/src/recon/llm/tenant_config_test.py
git commit -m "feat(llm): store admin preset overrides on the team LLM config" -m "Migration 0030 adds tenant_llm_config.preset_models (JSONB, ADD COLUMN IF NOT EXISTS for fresh DBs); the table's RLS covers it. save_config takes preset_models: omitted keeps the stored value, null/{} clears it, a map replaces it. A provider change with it omitted clears it, because the overrides are model ids for the old provider.

load_preset_context and team_key_provider read the provider and overrides without decrypting, so preset resolution can't fail on an undecryptable key.

Co-Authored-By: <the model you are>"
```

---

### Task 4: Cost estimate + `/settings/llm` preset views, `/models`, `preset_models` on PUT

**Files:**
- Create: `apps/platform/src/recon/llm/cost.py`
- Modify: `apps/platform/src/recon/llm/settings_router.py`
- Modify: `apps/platform/src/recon/llm/settings_router_test.py`

**Interfaces:**
- Consumes: `catalog.get_catalog`, `catalog.cached_ids`, `catalog.reset`, `catalog._client` (Task 2); `presets.PRESETS`, `presets.BUILTIN_PRESET_MODELS` (Task 1); `tenant_config.save_config(..., preset_models)`, `KEEP_PRESETS` (Task 3).
- Produces (HTTP):
  - `GET /settings/llm` adds `presets: {preset: {model, source: "team" | "builtin", available: bool | null}} | null` (null when no team config) and `builtin_preset_models`. `config.preset_models` comes from Task 3.
  - `GET /settings/llm/models` → `{available, stale, fetched_at, models, estimate: {prompt_tokens, completion_tokens, basis: "history" | "assumed", runs}}`
  - `PUT /settings/llm` accepts `preset_models` (keys ⊆ PRESETS, non-blank values) else 422.
- Produces (Python): `cost.estimate_tokens(tenant_id) -> dict`, `cost.ASSUMED_PROMPT_TOKENS = 20000`, `cost.ASSUMED_COMPLETION_TOKENS = 4000`.

- [ ] **Step 1: Write the failing tests.** Append to `llm/settings_router_test.py` (it has `client`, `team`, `_user`, `_auth`, `AUTH_KEY`):

```python
import httpx

from recon.db import models
from recon.db.base import tenant_session
from recon.llm import catalog
from recon.llm.catalog_test import SAMPLE
from recon.sessions import service as sessions_service


@pytest.fixture()
def stub_catalog(monkeypatch):
    catalog.reset()
    monkeypatch.setattr(
        catalog,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=SAMPLE))),
    )
    yield
    catalog.reset()


def test_models_endpoint_returns_filtered_catalog_and_assumed_estimate(client, team, stub_catalog):
    _, admin_h, _ = team
    body = client.get("/settings/llm/models", headers=admin_h).json()
    assert body["available"] is True
    assert len(body["models"]) == 4
    assert body["estimate"] == {"prompt_tokens": 20000, "completion_tokens": 4000, "basis": "assumed", "runs": 0}


def test_estimate_averages_real_runs_and_ignores_zero_token_rows(client, team, stub_catalog):
    tenant_id, admin_h, _ = team
    for prompt, completion in ((10000, 2000), (30000, 6000), (0, 0)):
        sv = sessions_service.create_session(tenant_id, name="e", scope_hosts=["acme.io"], authorized_by="t")
        with tenant_session(tenant_id) as db:
            db.add(models.SessionThreatModel(tenant_id=tenant_id, session_id=sv.id, status="done",
                                             prompt_tokens=prompt, completion_tokens=completion))
    est = client.get("/settings/llm/models", headers=admin_h).json()["estimate"]
    assert est == {"prompt_tokens": 20000, "completion_tokens": 4000, "basis": "history", "runs": 2}


def test_settings_show_builtin_then_team_presets_with_availability(client, team, stub_catalog):
    _, admin_h, _ = team
    assert client.get("/settings/llm", headers=admin_h).json()["presets"] is None
    client.put("/settings/llm", json={"provider": "openrouter", "model": "anthropic/claude-sonnet-4.6",
                                      "api_key": "k"}, headers=admin_h)
    client.get("/settings/llm/models", headers=admin_h)  # primes the catalog cache
    presets_view = client.get("/settings/llm", headers=admin_h).json()["presets"]
    assert presets_view["cheapest"] == {"model": "anthropic/claude-haiku-4.5", "source": "builtin", "available": True}
    client.put("/settings/llm", json={"provider": "openrouter", "model": "anthropic/claude-sonnet-4.6",
                                      "preset_models": {"strongest": "made/up-model"}}, headers=admin_h)
    body = client.get("/settings/llm", headers=admin_h).json()
    assert body["presets"]["strongest"] == {"model": "made/up-model", "source": "team", "available": False}
    assert body["config"]["preset_models"] == {"strongest": "made/up-model"}
    assert body["builtin_preset_models"]["openrouter"]["balanced"] == "anthropic/claude-sonnet-4.6"


def test_preset_models_validation(client, team):
    _, admin_h, _ = team
    base = {"provider": "anthropic", "model": "m", "api_key": "k"}
    assert client.put("/settings/llm", json={**base, "preset_models": {"fastest": "x"}}, headers=admin_h).status_code == 422
    assert client.put("/settings/llm", json={**base, "preset_models": {"cheapest": "  "}}, headers=admin_h).status_code == 422
    ok = client.put("/settings/llm", json={**base, "preset_models": {"cheapest": " claude-x "}}, headers=admin_h)
    assert ok.status_code == 200 and ok.json()["preset_models"] == {"cheapest": "claude-x"}
    cleared = client.put("/settings/llm", json={**base, "api_key": "", "preset_models": None}, headers=admin_h)
    assert cleared.json()["preset_models"] == {}
```

- [ ] **Step 2: Run to verify it fails** (integration; `src/recon/llm/settings_router_test.py`). Expected: 404 for `/settings/llm/models`, KeyError `presets`, 200 instead of 422.

- [ ] **Step 3: Implement.** `llm/cost.py`:

```python
"""Tokens per threat model, for the UI's cost estimate: the team's real history, else a
labelled assumption. Numbers come from data, not prose (CLAUDE.md §5)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select

from recon.db.base import tenant_session
from recon.db.models import SessionThreatModel

ASSUMED_PROMPT_TOKENS = 20000
ASSUMED_COMPLETION_TOKENS = 4000


def estimate_tokens(tenant_id: str) -> dict[str, Any]:
    with tenant_session(tenant_id) as db:
        avg_prompt, avg_completion, runs = db.execute(
            select(
                func.avg(SessionThreatModel.prompt_tokens),
                func.avg(SessionThreatModel.completion_tokens),
                func.count(),
            ).where(
                SessionThreatModel.tenant_id == uuid.UUID(tenant_id),
                SessionThreatModel.status == "done",
                # Some providers report 0 tokens when usage is missing; don't let that
                # drag the average toward free.
                SessionThreatModel.prompt_tokens > 0,
            )
        ).one()
    if not runs:
        return {
            "prompt_tokens": ASSUMED_PROMPT_TOKENS,
            "completion_tokens": ASSUMED_COMPLETION_TOKENS,
            "basis": "assumed",
            "runs": 0,
        }
    return {
        "prompt_tokens": round(avg_prompt),
        "completion_tokens": round(avg_completion or 0),
        "basis": "history",
        "runs": runs,
    }
```

`llm/settings_router.py`: add imports `from recon.llm import catalog, cost` and `from recon.llm.presets import BUILTIN_PRESET_MODELS, PRESETS`. Extend the request model:

```python
class TeamLlmConfigIn(BaseModel):
    provider: str
    model: str
    api_key: str = ""  # empty keeps the stored key
    # Omitted keeps the stored overrides; null or {} clears them (see model_fields_set).
    preset_models: dict[str, str] | None = None
```

Add the helper:

```python
def _preset_views(team_provider: str | None, overrides: dict[str, str]) -> dict | None:
    if team_provider is None:
        return None
    # Cache only: this route must never block on OpenRouter (only /models fetches).
    ids = catalog.cached_ids() if team_provider == "openrouter" else None
    views = {}
    for preset in PRESETS:
        model = overrides.get(preset) or BUILTIN_PRESET_MODELS[team_provider][preset]
        views[preset] = {
            "model": model,
            "source": "team" if overrides.get(preset) else "builtin",
            # A catalog variant ("x:free") is listed as-is; a routing variant isn't.
            "available": None if ids is None else (model in ids or model.split(":")[0] in ids),
        }
    return views
```

In `get_team_llm_settings`, add to the returned dict:

```python
        "presets": _preset_views(
            config["provider"] if config else None, (config or {}).get("preset_models") or {}
        ),
        "builtin_preset_models": BUILTIN_PRESET_MODELS,
```

In `save_team_llm_settings`, after the existing model check:

```python
    preset_models: object = tenant_config.KEEP_PRESETS
    if "preset_models" in body.model_fields_set:
        if body.preset_models is None:
            preset_models = None
        else:
            if set(body.preset_models) - set(PRESETS) or any(
                not v.strip() for v in body.preset_models.values()
            ):
                raise HTTPException(
                    status_code=422,
                    detail=f"preset_models keys must be among {list(PRESETS)} with non-empty model ids",
                )
            preset_models = {k: v.strip() for k, v in body.preset_models.items()}
```

and pass `preset_models` as the 6th positional argument to `tenant_config.save_config` in its `run_in_threadpool` call. Add the route:

```python
@router.get("/settings/llm/models")
async def list_llm_models(tenant_id: str = Depends(get_tenant_id)) -> dict:
    snapshot = await catalog.get_catalog()
    estimate = await run_in_threadpool(cost.estimate_tokens, tenant_id)
    return {**snapshot, "estimate": estimate}
```

- [ ] **Step 4: Run to verify it passes.** Paths `src/recon/llm/`. Lint + mypy.

- [ ] **Step 5: Commit**

```bash
git add apps/platform/src/recon/llm/cost.py apps/platform/src/recon/llm/settings_router.py apps/platform/src/recon/llm/settings_router_test.py
git commit -m "feat(api): model catalog, cost estimate and preset views in team LLM settings" -m "GET /settings/llm/models returns the filtered OpenRouter catalog plus a tokens-per-threat-model estimate: this team's real average over completed runs (zero-token rows ignored), else a labelled 20k/4k assumption. GET /settings/llm adds each preset's model (team override or built-in), its source, and catalog availability, read from the cache only so the page never blocks on OpenRouter. PUT accepts preset_models (keys among the three presets, non-blank ids): omitted keeps, null clears.

Co-Authored-By: <the model you are>"
```

---

### Task 5: Per-run preset in generation, atomic run claim, run-presets endpoint

**Files:**
- Modify: `apps/platform/src/recon/llm/service.py` (add `peek_credential_provider`)
- Create: `apps/platform/src/recon/llm/run_options.py`
- Modify: `apps/platform/src/recon/threat_model/service.py` (`run_generation`)
- Modify: `apps/platform/src/recon/threat_model/router.py`
- Modify: `apps/platform/src/recon/threat_model/service_test.py`
- Create: `apps/platform/src/recon/threat_model/router_test.py`

**Interfaces:**
- Consumes: `presets.resolve_model`, `presets.PRESETS` (Task 1); `tenant_config.load_preset_context`, `team_key_provider` (Task 3).
- Produces: `llm_service.peek_credential_provider(tenant_id, session_id) -> str | None`; `run_options.session_preset_options(tenant_id, session_id) -> {"credential_provider": str | None, "presets": {preset: model} | None}`; `run_generation(tenant_id, session_id, preset: str | None = None)`. HTTP: `POST /sessions/{id}/threat-model` accepts optional `{"preset": "cheapest" | "balanced" | "strongest"}` (else 422); `GET /sessions/{id}/threat-model/presets` → `session_preset_options`.

- [ ] **Step 1: Write the failing tests.** Append to `threat_model/service_test.py` (it already imports `asyncio`, `pytest`, `llm_service`, `sessions_service`, `tm_service`, and has `_session(tenant)`):

```python
import uuid

from recon.auth import service as auth_service
from recon.llm import tenant_config


def _no_env_keys(monkeypatch):
    for env_var in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(env_var, raising=False)


def _capture_models(monkeypatch) -> list:
    seen: list = []

    def fake_build(provider_name, api_key, model=None):
        seen.append((provider_name, model))
        raise RuntimeError("stub: stop before the LLM call")

    monkeypatch.setattr(tm_service, "build_provider", fake_build)
    monkeypatch.setattr(tm_service, "_assemble_context", lambda t, s: ("ctx", frozenset()))
    return seen


def test_preset_resolves_for_the_session_keys_provider(tenant, monkeypatch):
    _no_env_keys(monkeypatch)
    seen = _capture_models(monkeypatch)
    sid = _session(tenant)
    llm_service.save_config(tenant, sid, "openrouter", "anthropic/claude-sonnet-4.6", "k")
    tm_service.trigger_generation(tenant, sid)
    asyncio.run(tm_service.run_generation(tenant, sid, "cheapest"))
    assert seen == [("openrouter", "anthropic/claude-haiku-4.5:floor")]


def test_team_override_used_for_the_team_key(tenant, monkeypatch):
    _no_env_keys(monkeypatch)
    seen = _capture_models(monkeypatch)
    admin = auth_service.seed_admin(
        username=f"a-{uuid.uuid4().hex[:8]}", password="pw", tenant_id=tenant, tenant_name="t", role="admin"
    )
    tenant_config.save_config(tenant, admin, "openrouter", "team-model", "k", {"strongest": "vendor/best"})
    sid = _session(tenant)
    tm_service.trigger_generation(tenant, sid)
    asyncio.run(tm_service.run_generation(tenant, sid, "strongest"))
    assert seen == [("openrouter", "vendor/best")]


def test_no_preset_keeps_the_saved_model(tenant, monkeypatch):
    _no_env_keys(monkeypatch)
    seen = _capture_models(monkeypatch)
    sid = _session(tenant)
    llm_service.save_config(tenant, sid, "anthropic", "claude-x", "k")
    tm_service.trigger_generation(tenant, sid)
    asyncio.run(tm_service.run_generation(tenant, sid))
    assert seen == [("anthropic", "claude-x")]


def test_a_run_is_claimed_once(tenant, monkeypatch):
    _no_env_keys(monkeypatch)
    seen = _capture_models(monkeypatch)
    sid = _session(tenant)
    llm_service.save_config(tenant, sid, "anthropic", "claude-x", "k")
    tm_service.trigger_generation(tenant, sid)
    asyncio.run(tm_service.run_generation(tenant, sid))
    asyncio.run(tm_service.run_generation(tenant, sid))  # no longer pending → no-op
    assert len(seen) == 1


def test_running_row_is_not_reclaimed(tenant, monkeypatch):
    _no_env_keys(monkeypatch)
    seen = _capture_models(monkeypatch)
    sid = _session(tenant)
    llm_service.save_config(tenant, sid, "anthropic", "claude-x", "k")
    tm_service.trigger_generation(tenant, sid)
    tm_service._set_status(tenant, sid, "running")
    asyncio.run(tm_service.run_generation(tenant, sid))
    assert seen == []
    assert tm_service.get_threat_model(tenant, sid)["status"] == "running"
```

`threat_model/router_test.py`:

```python
"""Threat-model routes: preset validation and the per-run presets endpoint."""

import pytest
from fastapi.testclient import TestClient

from recon.api.app import create_app
from recon.llm import service as llm_service
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


@pytest.fixture()
def client():
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _no_env_keys(monkeypatch):
    for env_var in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(env_var, raising=False)


def _session(tenant: str) -> str:
    return sessions_service.create_session(tenant, name="e", scope_hosts=["acme.io"], authorized_by="t").id


def test_unknown_preset_is_422(client, tenant):
    sid = _session(tenant)
    r = client.post(f"/sessions/{sid}/threat-model", json={"preset": "turbo"}, headers={"X-Tenant-Id": tenant})
    assert r.status_code == 422


def test_presets_endpoint_resolves_for_the_credential_provider(client, tenant):
    sid = _session(tenant)
    llm_service.save_config(tenant, sid, "openrouter", "anthropic/claude-sonnet-4.6", "k")
    r = client.get(f"/sessions/{sid}/threat-model/presets", headers={"X-Tenant-Id": tenant})
    assert r.status_code == 200
    assert r.json() == {
        "credential_provider": "openrouter",
        "presets": {
            "cheapest": "anthropic/claude-haiku-4.5:floor",
            "balanced": "anthropic/claude-sonnet-4.6",
            "strongest": "anthropic/claude-opus-5.5",
        },
    }


def test_presets_endpoint_without_any_key(client, tenant):
    sid = _session(tenant)
    r = client.get(f"/sessions/{sid}/threat-model/presets", headers={"X-Tenant-Id": tenant})
    assert r.json() == {"credential_provider": None, "presets": None}
```

- [ ] **Step 2: Run to verify they fail** (integration; `src/recon/threat_model/`). Expected: `TypeError` (run_generation takes 2 args), `test_a_run_is_claimed_once` sees 2 calls, router 404s.

- [ ] **Step 3: Implement.** `llm/service.py`, after `load_credentials`:

```python
def peek_credential_provider(tenant_id: str, session_id: str) -> str | None:
    """Which provider load_credentials would use, without decrypting anything (for the UI)."""
    try:
        resolved = _resolve_session_id(tenant_id, session_id)
    except ValueError:
        return None
    with tenant_session(tenant_id) as db:
        row = (
            db.query(SessionLlmConfig)
            .filter_by(session_id=uuid.UUID(resolved), tenant_id=uuid.UUID(tenant_id))
            .first()
        )
        if row is not None and row.encrypted_api_key:
            return row.provider
    team = tenant_config.team_key_provider(tenant_id)
    if team is not None:
        return team
    for provider, env_var in _ENV_KEYS:
        if os.environ.get(env_var):
            return provider
    return None
```

`llm/run_options.py`:

```python
"""What each preset would run for one session: the credential's provider decides, so the
Threat Model tab never shows a model the run won't actually use."""

from __future__ import annotations

from typing import Any

from recon.llm import service as llm_service
from recon.llm import tenant_config
from recon.llm.presets import PRESETS, resolve_model


def session_preset_options(tenant_id: str, session_id: str) -> dict[str, Any]:
    provider = llm_service.peek_credential_provider(tenant_id, session_id)
    if provider is None:
        return {"credential_provider": None, "presets": None}
    team_provider, overrides = tenant_config.load_preset_context(tenant_id)
    return {
        "credential_provider": provider,
        "presets": {p: resolve_model(p, provider, team_provider, overrides, None) for p in PRESETS},
    }
```

`threat_model/service.py`: import `from sqlalchemy import update` (if absent), `from recon.llm import tenant_config`, `from recon.llm.presets import resolve_model`. Change the signature to `async def run_generation(tenant_id: str, session_id: str, preset: str | None = None) -> None:` and update the docstring to mention the preset. Replace the guard block (the `with tenant_session(...)` query for `guard` + `if guard is None or guard.status == "running": return` + the following `_set_status(tenant_id, resolved, "running")`) with:

```python
    # Claim the run atomically: only a pending row flips to running, so a re-trigger (or
    # two queued tasks) can't start two paid LLM calls.
    with tenant_session(tenant_id) as db:
        claimed = db.execute(
            update(SessionThreatModel)
            .where(
                SessionThreatModel.session_id == uuid.UUID(resolved),
                SessionThreatModel.tenant_id == uuid.UUID(tenant_id),
                SessionThreatModel.status == "pending",
            )
            .values(status="running", updated_at=dt.datetime.now(dt.UTC))
            .returning(SessionThreatModel.id)
        ).first()
    if claimed is None:
        return
```

Inside the existing `try:` around `llm_service.load_credentials`, replace the single line with:

```python
        credentials = llm_service.load_credentials(tenant_id, resolved)
        if credentials is not None:
            # Inside the try: a failure here must mark the run failed, not leave it running.
            team_provider, overrides = tenant_config.load_preset_context(tenant_id)
            cred_provider, saved_model, cred_key = credentials
            credentials = (
                cred_provider,
                resolve_model(preset, cred_provider, team_provider, overrides, saved_model),
                cred_key,
            )
```

After `provider_name, model_name, api_key = credentials` add:

```python
    log.info(
        "threat_model.generation_model",
        tenant_id=tenant_id,
        session_id=resolved,
        preset=preset,
        provider=provider_name,
        model=model_name,
    )
```

`threat_model/router.py`: add imports `from typing import Literal`, `from fastapi import Body`, `from pydantic import BaseModel`, `from recon.llm import run_options`, and:

```python
class ThreatModelTriggerIn(BaseModel):
    preset: Literal["cheapest", "balanced", "strongest"] | None = None
```

Change `trigger_threat_model` to accept `body: ThreatModelTriggerIn | None = Body(default=None)`, and pass the preset to the task: `background_tasks.add_task(service.run_generation, tenant_id, session_id, body.preset if body else None)`. Add the route (before the `_run_in_thread` helper section):

```python
@router.get("/sessions/{session_id}/threat-model/presets")
async def get_threat_model_presets(
    session_id: str,
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    """The model each preset would run for this session's credential (no key material)."""
    return await _run_in_thread(run_options.session_preset_options, tenant_id, session_id)
```

- [ ] **Step 4: Run to verify they pass.** Paths `src/recon/threat_model/ src/recon/llm/ src/recon/db/llm_threat_model_rls_test.py`. Lint.

- [ ] **Step 5: Commit**

```bash
git add apps/platform/src/recon/llm/service.py apps/platform/src/recon/llm/run_options.py apps/platform/src/recon/threat_model/
git commit -m "feat(threat-model): per-run model preset and an atomic run claim" -m "POST /sessions/{id}/threat-model takes an optional {preset} (cheapest/balanced/strongest). run_generation resolves it against the credential's own provider, applying team overrides only for the team provider and :floor for Cheapest on OpenRouter. It runs inside the existing failure handling, so a lookup error marks the run failed. The chosen preset and model are logged as threat_model.generation_model.

The pending->running claim is now one UPDATE ... WHERE status='pending' RETURNING. Before, a re-trigger while pending queued a second task, and the check-then-set guard let both make paid LLM calls.

GET /sessions/{id}/threat-model/presets returns the credential's provider and each preset's model, without decrypting anything, so the UI shows what will actually run.

Co-Authored-By: <the model you are>"
```

---

### Task 6: Web — shared catalog helpers, ModelPicker, presets in Settings

**Files:**
- Create: `apps/platform/web/src/api/llmCatalog.ts`
- Create: `apps/platform/web/src/api/llmCatalog.test.ts`
- Modify: `apps/platform/web/src/features/settings/settingsApi.ts`
- Create: `apps/platform/web/src/features/settings/ModelPicker.tsx`
- Create: `apps/platform/web/src/features/settings/ModelPicker.test.tsx`
- Create: `apps/platform/web/src/features/settings/PresetRows.tsx`
- Create: `apps/platform/web/src/features/settings/PresetRows.test.tsx`
- Modify: `apps/platform/web/src/features/settings/SettingsPage.tsx`
- Modify: `apps/platform/web/src/features/settings/SettingsPage.test.tsx` (fixtures gain the new fields; mock the catalog)
- Modify: `apps/platform/web/src/features/settings/settings.css`

**Interfaces:**
- Consumes: Task 4 HTTP API.
- Produces (`src/api/llmCatalog.ts`): `PRESETS`, `type Preset`, `PRESET_LABELS`, `CatalogModel`, `CostEstimate`, `ModelCatalog`, `getModelCatalog(tenantId)`, `costPerRun(model, estimate)`, `formatCost(usd)`, `findModel(catalog, id)`, `costLabel(catalog, id)`. Task 7 consumes these.

- [ ] **Step 1: Write the failing tests.**

`src/api/llmCatalog.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { costLabel, findModel, formatCost, type ModelCatalog } from "./llmCatalog";

const CATALOG: ModelCatalog = {
  available: true, stale: false, fetched_at: null,
  estimate: { prompt_tokens: 20000, completion_tokens: 4000, basis: "assumed", runs: 0 },
  models: [
    { id: "anthropic/claude-haiku-4.5", name: "Claude Haiku 4.5", context_length: 200000, max_completion_tokens: 64000, prompt_price: "0.000001", completion_price: "0.000005" },
    { id: "apodex/apodex-1.1-mini:free", name: "Apodex Mini (free)", context_length: 262144, max_completion_tokens: null, prompt_price: "0", completion_price: "0" },
  ],
};

describe("llmCatalog", () => {
  it("formats cost", () => {
    expect(formatCost(0)).toBe("free");
    expect(formatCost(0.004)).toBe("<$0.01");
    expect(formatCost(0.123)).toBe("≈ $0.12");
  });
  it("finds a routing variant by its base id, and a catalog variant as-is", () => {
    expect(findModel(CATALOG, "anthropic/claude-haiku-4.5:floor")?.id).toBe("anthropic/claude-haiku-4.5");
    expect(findModel(CATALOG, "apodex/apodex-1.1-mini:free")?.id).toBe("apodex/apodex-1.1-mini:free");
    expect(findModel(CATALOG, "nope/model")).toBeUndefined();
  });
  it("labels cost per threat model from the estimate", () => {
    // 20000 × 0.000001 + 4000 × 0.000005 = 0.04
    expect(costLabel(CATALOG, "anthropic/claude-haiku-4.5:floor")).toBe("≈ $0.04");
    expect(costLabel(null, "anthropic/claude-haiku-4.5")).toBeNull();
  });
});
```

`features/settings/ModelPicker.test.tsx`:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ModelPicker } from "./ModelPicker";
import type { ModelCatalog } from "../../api/llmCatalog";

const CATALOG: ModelCatalog = {
  available: true, stale: false, fetched_at: null,
  estimate: { prompt_tokens: 10000, completion_tokens: 2000, basis: "history", runs: 3 },
  models: [
    { id: "anthropic/claude-opus-5.5", name: "Claude Opus 5.5", context_length: 1000000, max_completion_tokens: 128000, prompt_price: "0.000004", completion_price: "0.00002" },
    { id: "apodex/apodex-1.1-mini:free", name: "Apodex Mini (free)", context_length: 262144, max_completion_tokens: null, prompt_price: "0", completion_price: "0" },
  ],
};

describe("ModelPicker", () => {
  it("lists cheapest first with costs and says what the estimate is based on", () => {
    render(<ModelPicker catalog={CATALOG} title="Pick" onPick={vi.fn()} onClose={vi.fn()} />);
    const rows = screen.getAllByRole("button", { name: /ctx/ });
    expect(rows[0]).toHaveTextContent("Apodex Mini (free)");
    expect(rows[0]).toHaveTextContent("free");
    expect(rows[1]).toHaveTextContent("≈ $0.08");
    expect(screen.getByText(/based on your last 3 runs/)).toBeInTheDocument();
  });
  it("filters by search and reports the pick", async () => {
    const onPick = vi.fn();
    render(<ModelPicker catalog={CATALOG} title="Pick" onPick={onPick} onClose={vi.fn()} />);
    await userEvent.type(screen.getByLabelText("Search models"), "opus");
    const rows = screen.getAllByRole("button", { name: /ctx/ });
    expect(rows).toHaveLength(1);
    await userEvent.click(rows[0]);
    expect(onPick).toHaveBeenCalledWith("anthropic/claude-opus-5.5");
  });
  it("says so when the catalog is unavailable", () => {
    render(<ModelPicker catalog={{ ...CATALOG, available: false, models: [] }} title="Pick" onPick={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByText(/catalog is unavailable/)).toBeInTheDocument();
  });
});
```

`features/settings/PresetRows.test.tsx`:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PresetRows } from "./PresetRows";
import type { TeamLlmSettings } from "./settingsApi";
import type { ModelCatalog } from "../../api/llmCatalog";

const CATALOG: ModelCatalog = {
  available: true, stale: false, fetched_at: null,
  estimate: { prompt_tokens: 20000, completion_tokens: 4000, basis: "assumed", runs: 0 },
  models: [
    { id: "anthropic/claude-haiku-4.5", name: "Claude Haiku 4.5", context_length: 200000, max_completion_tokens: 64000, prompt_price: "0.000001", completion_price: "0.000005" },
    { id: "anthropic/claude-opus-5.5", name: "Claude Opus 5.5", context_length: 1000000, max_completion_tokens: 128000, prompt_price: "0.000004", completion_price: "0.00002" },
  ],
};

function settings(over: Partial<TeamLlmSettings> = {}): TeamLlmSettings {
  return {
    can_edit: true,
    default_models: {},
    providers: ["anthropic", "gemini", "openrouter"],
    builtin_preset_models: {},
    config: {
      provider: "openrouter", model: "anthropic/claude-sonnet-4.6", has_key: true,
      configured_at: null, configured_by: null, tested_at: null, preset_models: { strongest: "vendor/big" },
    },
    presets: {
      cheapest: { model: "anthropic/claude-haiku-4.5", source: "builtin", available: true },
      balanced: { model: "anthropic/claude-sonnet-4.6", source: "builtin", available: null },
      strongest: { model: "vendor/big", source: "team", available: false },
    },
    ...over,
  };
}

describe("PresetRows", () => {
  it("shows each preset's model, source, cost and catalog warnings", () => {
    render(<PresetRows settings={settings()} catalog={CATALOG} busy={false} onSave={vi.fn()} />);
    expect(screen.getByText("anthropic/claude-haiku-4.5")).toBeInTheDocument();
    expect(screen.getByText("≈ $0.04")).toBeInTheDocument();
    expect(screen.getAllByText("default")).toHaveLength(2);
    expect(screen.getByText("team")).toBeInTheDocument();
    expect(screen.getByText("not in catalog")).toBeInTheDocument();
  });
  it("editing a preset through the picker saves the full override map", async () => {
    const onSave = vi.fn();
    render(<PresetRows settings={settings()} catalog={CATALOG} busy={false} onSave={onSave} />);
    await userEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    await userEvent.click(screen.getByRole("button", { name: /Claude Opus 5.5/ }));
    expect(onSave).toHaveBeenCalledWith({ strongest: "vendor/big", cheapest: "anthropic/claude-opus-5.5" });
  });
  it("reset removes the override, clearing the map when it was the last one", async () => {
    const onSave = vi.fn();
    render(<PresetRows settings={settings()} catalog={CATALOG} busy={false} onSave={onSave} />);
    await userEvent.click(screen.getByRole("button", { name: "Reset" }));
    expect(onSave).toHaveBeenCalledWith(null);
  });
  it("analysts see presets read-only", () => {
    render(<PresetRows settings={settings({ can_edit: false })} catalog={CATALOG} busy={false} onSave={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  });
  it("non-OpenRouter teams edit a preset as text", async () => {
    const onSave = vi.fn();
    const s = settings();
    s.config = { ...s.config!, provider: "anthropic", preset_models: {} };
    render(<PresetRows settings={s} catalog={null} busy={false} onSave={onSave} />);
    await userEvent.click(screen.getAllByRole("button", { name: "Edit" })[1]);
    const input = screen.getByLabelText(/Balanced model/);
    await userEvent.clear(input);
    await userEvent.type(input, "claude-y");
    await userEvent.click(screen.getByRole("button", { name: "Save preset" }));
    expect(onSave).toHaveBeenCalledWith({ balanced: "claude-y" });
  });
});
```

In `SettingsPage.test.tsx`: add `builtin_preset_models: {}, presets: null` to `BASE`, `preset_models: {}` to `SAVED.config`, `import * as catalogApi from "../../api/llmCatalog";` and in `beforeEach`, after `vi.restoreAllMocks()`: `vi.spyOn(catalogApi, "getModelCatalog").mockResolvedValue({ available: false, stale: false, fetched_at: null, models: [], estimate: { prompt_tokens: 20000, completion_tokens: 4000, basis: "assumed", runs: 0 } });`

- [ ] **Step 2: Run to verify they fail.** `npm test -- --run src/api/llmCatalog.test.ts src/features/settings`. Expected: unresolved imports.

- [ ] **Step 3: Implement.**

`src/api/llmCatalog.ts`:

```ts
import { request } from "./apiClient";

// Shared by Settings and the Threat Model tab (features don't import each other).
export const PRESETS = ["cheapest", "balanced", "strongest"] as const;
export type Preset = (typeof PRESETS)[number];
export const PRESET_LABELS: Record<Preset, string> = { cheapest: "Cheapest", balanced: "Balanced", strongest: "Strongest" };

export interface CatalogModel {
  id: string;
  name: string;
  context_length: number;
  max_completion_tokens: number | null;
  prompt_price: string; // USD per token
  completion_price: string;
}
export interface CostEstimate { prompt_tokens: number; completion_tokens: number; basis: "history" | "assumed"; runs: number }
export interface ModelCatalog { available: boolean; stale: boolean; fetched_at: string | null; models: CatalogModel[]; estimate: CostEstimate }

export function getModelCatalog(tenantId: string): Promise<ModelCatalog> {
  return request("/settings/llm/models", {}, tenantId);
}

export function costPerRun(m: CatalogModel, est: CostEstimate): number {
  return Number(m.prompt_price) * est.prompt_tokens + Number(m.completion_price) * est.completion_tokens;
}

export function formatCost(usd: number): string {
  if (usd === 0) return "free";
  if (usd < 0.01) return "<$0.01";
  return `≈ $${usd.toFixed(2)}`;
}

// Routing variants (":floor") aren't catalog entries; catalog variants (":free") are.
export function findModel(catalog: ModelCatalog | null, id: string): CatalogModel | undefined {
  if (!catalog) return undefined;
  return catalog.models.find((m) => m.id === id) ?? catalog.models.find((m) => m.id === id.split(":")[0]);
}

export function costLabel(catalog: ModelCatalog | null, id: string): string | null {
  const model = findModel(catalog, id);
  return model && catalog ? formatCost(costPerRun(model, catalog.estimate)) : null;
}
```

`features/settings/settingsApi.ts`: add `import type { Preset } from "../../api/llmCatalog";`, add `preset_models: Record<string, string>;` to `TeamLlmConfig`, add to `TeamLlmSettings`:

```ts
  presets: Record<Preset, PresetView> | null;
  builtin_preset_models: Record<string, Record<string, string>>;
```

and

```ts
export interface PresetView { model: string; source: "team" | "builtin"; available: boolean | null }
```

Change `saveTeamLlmSettings`'s body type to `{ provider: string; model: string; api_key: string; preset_models?: Record<string, string> | null }`.

`features/settings/ModelPicker.tsx`:

```tsx
import { useMemo, useState } from "react";
import { costPerRun, formatCost, type CatalogModel, type ModelCatalog } from "../../api/llmCatalog";

const MAX_ROWS = 200; // the catalog has hundreds of models; search narrows it

export function ModelPicker({ catalog, title, onPick, onClose }: {
  catalog: ModelCatalog; title: string; onPick: (id: string) => void; onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<"price" | "name">("price");
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    const cost = (m: CatalogModel) => costPerRun(m, catalog.estimate);
    return catalog.models
      .filter((m) => !q || m.id.toLowerCase().includes(q) || m.name.toLowerCase().includes(q))
      .sort((a, b) => (sort === "price" ? cost(a) - cost(b) : a.name.localeCompare(b.name)));
  }, [catalog, query, sort]);
  const basis = catalog.estimate.basis === "history"
    ? `based on your last ${catalog.estimate.runs} runs`
    : "assumed, no history yet";

  return (
    <div className="model-picker" role="dialog" aria-label={title}>
      <div className="model-picker-head">
        <strong>{title}</strong>
        <button type="button" className="shell-btn" onClick={onClose}>Close</button>
      </div>
      {!catalog.available ? (
        <p className="settings-error">The OpenRouter catalog is unavailable right now. Type a model ID instead.</p>
      ) : (
        <>
          <div className="model-picker-controls">
            <input aria-label="Search models" placeholder="Search by name or ID" value={query}
              onChange={(e) => setQuery(e.target.value)} />
            <select aria-label="Sort models" value={sort} onChange={(e) => setSort(e.target.value as "price" | "name")}>
              <option value="price">Cheapest first</option>
              <option value="name">Name</option>
            </select>
          </div>
          <p className="settings-hint">
            Cost per threat model, {basis}{catalog.stale ? " · catalog may be out of date" : ""}.
          </p>
          <ul className="model-picker-list">
            {rows.slice(0, MAX_ROWS).map((m) => (
              <li key={m.id}>
                <button type="button" onClick={() => onPick(m.id)}>
                  <span className="model-picker-name">{m.name}</span>
                  <code>{m.id}</code>
                  <span>{Math.round(m.context_length / 1000)}k ctx</span>
                  <span>{formatCost(costPerRun(m, catalog.estimate))}</span>
                </button>
              </li>
            ))}
          </ul>
          {rows.length > MAX_ROWS && <p className="settings-hint">Showing {MAX_ROWS} of {rows.length}; refine the search.</p>}
        </>
      )}
    </div>
  );
}
```

`features/settings/PresetRows.tsx`:

```tsx
import { useState } from "react";
import { costLabel, PRESET_LABELS, PRESETS, type ModelCatalog, type Preset } from "../../api/llmCatalog";
import { ModelPicker } from "./ModelPicker";
import type { TeamLlmSettings } from "./settingsApi";

export function PresetRows({ settings, catalog, busy, onSave }: {
  settings: TeamLlmSettings;
  catalog: ModelCatalog | null;
  busy: boolean;
  onSave: (presetModels: Record<string, string> | null) => void;
}) {
  const [editing, setEditing] = useState<Preset | null>(null);
  const [draft, setDraft] = useState("");
  const cfg = settings.config;
  const views = settings.presets;
  if (!cfg || !views) return null;
  const overrides = cfg.preset_models ?? {};
  const pickable = cfg.provider === "openrouter" && !!catalog?.available;

  const save = (preset: Preset, model: string | null) => {
    const next: Record<string, string> = { ...overrides };
    if (model) next[preset] = model;
    else delete next[preset];
    setEditing(null);
    onSave(Object.keys(next).length ? next : null);
  };

  return (
    <section className="settings-presets" aria-labelledby="presets-title">
      <h3 id="presets-title" className="settings-subtitle">Presets</h3>
      <p className="settings-hint">Anyone on the team can pick a preset when generating a threat model.</p>
      <ul className="settings-preset-list">
        {PRESETS.map((preset) => {
          const view = views[preset];
          const cost = cfg.provider === "openrouter" ? costLabel(catalog, view.model) : null;
          return (
            <li key={preset} className="settings-preset">
              <span className="settings-preset-name">{PRESET_LABELS[preset]}</span>
              <code>{view.model}</code>
              <span className="settings-badge">{view.source === "team" ? "team" : "default"}</span>
              {view.available === false && <span className="settings-error">not in catalog</span>}
              {cost && <span>{cost}</span>}
              {settings.can_edit && (
                <span className="settings-actions">
                  <button type="button" className="shell-btn" disabled={busy}
                    onClick={() => { setDraft(view.model); setEditing(preset); }}>Edit</button>
                  {view.source === "team" && (
                    <button type="button" className="shell-btn" disabled={busy} onClick={() => save(preset, null)}>Reset</button>
                  )}
                </span>
              )}
            </li>
          );
        })}
      </ul>
      {editing && pickable && catalog && (
        <ModelPicker catalog={catalog} title={`${PRESET_LABELS[editing]} model`}
          onPick={(id) => save(editing, id)} onClose={() => setEditing(null)} />
      )}
      {editing && !pickable && (
        <form className="settings-form" onSubmit={(e) => { e.preventDefault(); if (draft.trim()) save(editing, draft.trim()); }}>
          <label>
            {PRESET_LABELS[editing]} model
            <input value={draft} onChange={(e) => setDraft(e.target.value)} />
          </label>
          <div className="settings-actions">
            <button type="submit" className="btn-primary" disabled={busy || !draft.trim()}>Save preset</button>
            <button type="button" className="shell-btn" onClick={() => setEditing(null)}>Cancel</button>
          </div>
        </form>
      )}
    </section>
  );
}
```

`features/settings/SettingsPage.tsx`:
- Imports: `import { getModelCatalog, type ModelCatalog } from "../../api/llmCatalog";`, `import { ModelPicker } from "./ModelPicker";`, `import { PresetRows } from "./PresetRows";`.
- State: `const [catalog, setCatalog] = useState<ModelCatalog | null>(null);` and `const [pickingModel, setPickingModel] = useState(false);`.
- Add an effect after the existing load effect:

```tsx
  useEffect(() => {
    // Separate from the settings load so an unreachable OpenRouter never delays the page.
    // Once the catalog is cached, refresh just the preset availability flags.
    getModelCatalog(tenantId)
      .then((c) => {
        setCatalog(c);
        return getTeamLlmSettings(tenantId).then((s) =>
          setSettings((prev) => (prev ? { ...prev, presets: s.presets } : prev)),
        );
      })
      .catch(() => setCatalog(null));
  }, [tenantId]);
```

- In the admin form, right after the Model `<label>`, add:

```tsx
          {provider === "openrouter" && catalog?.available && (
            <button type="button" className="shell-btn" onClick={() => setPickingModel(true)}>Choose from catalog…</button>
          )}
          {pickingModel && catalog && (
            <ModelPicker catalog={catalog} title="Team default model"
              onPick={(id) => { setModel(id); setPickingModel(false); }} onClose={() => setPickingModel(false)} />
          )}
```

- After the `{cfg && (<p className="settings-meta">…)}` block, add:

```tsx
      <PresetRows
        settings={settings}
        catalog={catalog}
        busy={busy}
        onSave={(presetModels) => {
          if (!cfg) return;
          // The saved provider/model, not unsaved form edits: this PUT changes only presets.
          void run(
            () => saveTeamLlmSettings(tenantId, { provider: cfg.provider, model: cfg.model, api_key: "", preset_models: presetModels }),
            "Presets saved",
          );
        }}
      />
```

`features/settings/settings.css`: append

```css
.settings-subtitle { font-size: 14px; margin: 18px 0 4px; }
.settings-preset-list { list-style: none; padding: 0; margin: 0; display: grid; gap: 6px; }
.settings-preset { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; min-width: 0; }
.settings-preset code { overflow-wrap: anywhere; }
.settings-preset-name { min-width: 80px; font-weight: 500; }
.settings-badge { font-size: 11px; padding: 1px 6px; border-radius: 6px; border: 1px solid var(--line, #2a3243); }
.model-picker { margin-top: 10px; border: 1px solid var(--line, #2a3243); border-radius: 10px; padding: 10px; }
.model-picker-head, .model-picker-controls { display: flex; gap: 8px; align-items: center; justify-content: space-between; }
.model-picker-controls input { flex: 1; min-width: 0; }
.model-picker-list { list-style: none; padding: 0; margin: 8px 0 0; max-height: 320px; overflow: auto; }
.model-picker-list button { display: grid; grid-template-columns: 1fr auto auto; gap: 2px 10px; width: 100%; text-align: left; padding: 6px; background: none; border: 0; color: inherit; cursor: pointer; }
.model-picker-list button code { grid-column: 1 / -1; overflow-wrap: anywhere; font-size: 12px; opacity: 0.8; }
.model-picker-name { font-weight: 500; }
```

- [ ] **Step 4: Run to verify they pass.** `npm test -- --run` (full suite), `npm run lint` (0 errors), `npm run build`. Check `wc -l` keeps every file under ~300 lines.

- [ ] **Step 5: Commit**

```bash
git add apps/platform/web/src/api/llmCatalog.ts apps/platform/web/src/api/llmCatalog.test.ts apps/platform/web/src/features/settings/
git commit -m "feat(web): OpenRouter model picker and preset editing in team Settings" -m "Settings shows the Cheapest/Balanced/Strongest presets, each with its model, team/default badge, catalog warning and a cost per threat model (live price times the team's real token history). Admins edit a preset through a searchable, price-sorted picker over the live OpenRouter catalog (or a text field for direct providers) and can reset it; analysts see it read-only. The team default model gets a 'Choose from catalog' picker too.

Shared catalog types and cost helpers live in src/api/llmCatalog.ts, because Settings and the Threat Model tab both use them and features don't import each other. The catalog loads separately from the settings, so an unreachable OpenRouter never delays the page.

Co-Authored-By: <the model you are>"
```

---

### Task 7: Web — preset choice on the Threat Model tab

**Files:**
- Create: `apps/platform/web/src/features/threat-model/threatModelApi.ts`
- Modify: `apps/platform/web/src/api/apiClient.ts` (`triggerThreatModel` optional `preset`)
- Modify: `apps/platform/web/src/features/threat-model/ThreatModelPage.tsx`
- Modify: `apps/platform/web/src/features/threat-model/ThreatModelPage.test.tsx`

**Interfaces:**
- Consumes: `GET /sessions/{id}/threat-model/presets` (Task 5); `PRESETS`, `PRESET_LABELS`, `getModelCatalog`, `costLabel`, types from `src/api/llmCatalog.ts` (Task 6).
- Produces: `getRunPresets(tenantId, sessionId): Promise<RunPresets>`; `triggerThreatModel(tenantId, sessionId, preset?)`.

- [ ] **Step 1: Write the failing tests.** In `ThreatModelPage.test.tsx` add imports and a default mock, then the tests:

```tsx
import userEvent from "@testing-library/user-event";
import * as tmApi from "./threatModelApi";
import * as catalogApi from "../../api/llmCatalog";

const CATALOG = {
  available: true, stale: false, fetched_at: null,
  estimate: { prompt_tokens: 20000, completion_tokens: 4000, basis: "assumed" as const, runs: 0 },
  models: [{ id: "anthropic/claude-haiku-4.5", name: "Haiku", context_length: 200000, max_completion_tokens: 64000, prompt_price: "0.000001", completion_price: "0.000005" }],
};
const OR_PRESETS = {
  credential_provider: "openrouter",
  presets: { cheapest: "anthropic/claude-haiku-4.5:floor", balanced: "anthropic/claude-sonnet-4.6", strongest: "anthropic/claude-opus-5.5" },
};
```

In `beforeEach`, after `vi.restoreAllMocks()`: `vi.spyOn(tmApi, "getRunPresets").mockResolvedValue({ credential_provider: null, presets: null });` and `vi.spyOn(catalogApi, "getModelCatalog").mockResolvedValue(CATALOG);`. Tests:

```tsx
  it("offers presets with costs for an OpenRouter key and sends the chosen one", async () => {
    vi.spyOn(api, "getThreatModel").mockRejectedValue(Object.assign(new Error("nf"), { status: 404 }));
    vi.spyOn(tmApi, "getRunPresets").mockResolvedValue(OR_PRESETS);
    const trigger = vi.spyOn(api, "triggerThreatModel").mockResolvedValue({ ...failed("x"), status: "pending" });
    render(<MemoryRouter><ThreatModelPage sessionId="s1" /></MemoryRouter>);
    const select = await screen.findByLabelText("Model preset");
    expect(await screen.findByRole("option", { name: /Cheapest · anthropic\/claude-haiku-4.5:floor · ≈ \$0.04/ })).toBeInTheDocument();
    await userEvent.selectOptions(select, "strongest");
    await userEvent.click(screen.getByRole("button", { name: /Generate Threat Model/ }));
    expect(trigger).toHaveBeenCalledWith("t1", "s1", "strongest");
  });

  it("no costs for a direct provider, and Default sends no preset", async () => {
    vi.spyOn(api, "getThreatModel").mockRejectedValue(Object.assign(new Error("nf"), { status: 404 }));
    vi.spyOn(tmApi, "getRunPresets").mockResolvedValue({
      credential_provider: "anthropic",
      presets: { cheapest: "claude-haiku-4-5-20251001", balanced: "claude-sonnet-4-6", strongest: "claude-opus-5-5" },
    });
    const trigger = vi.spyOn(api, "triggerThreatModel").mockResolvedValue({ ...failed("x"), status: "pending" });
    render(<MemoryRouter><ThreatModelPage sessionId="s1" /></MemoryRouter>);
    expect(await screen.findByRole("option", { name: "Strongest · claude-opus-5-5" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Generate Threat Model/ }));
    expect(trigger).toHaveBeenCalledWith("t1", "s1", undefined);
  });

  it("the empty state points to Settings", async () => {
    vi.spyOn(api, "getThreatModel").mockRejectedValue(Object.assign(new Error("nf"), { status: 404 }));
    render(<MemoryRouter><ThreatModelPage sessionId="s1" /></MemoryRouter>);
    expect(await screen.findByRole("link", { name: "Settings" })).toHaveAttribute("href", "/settings");
  });
```

- [ ] **Step 2: Run to verify they fail.** `npm test -- --run src/features/threat-model` → unresolved `./threatModelApi`.

- [ ] **Step 3: Implement.**

`features/threat-model/threatModelApi.ts`:

```ts
import { request } from "../../api/apiClient";
import type { Preset } from "../../api/llmCatalog";

export interface RunPresets { credential_provider: string | null; presets: Record<Preset, string> | null }

// What each preset would actually run for this session's credential (no key material).
export function getRunPresets(tenantId: string, sessionId: string): Promise<RunPresets> {
  return request(`/sessions/${encodeURIComponent(sessionId)}/threat-model/presets`, {}, tenantId);
}
```

`api/apiClient.ts`, replace `triggerThreatModel` with:

```ts
export function triggerThreatModel(tenantId: string, sessionId: string, preset?: string): Promise<import("./types").ThreatModelResponse> {
  return request(`/sessions/${encodeURIComponent(sessionId)}/threat-model`, preset ? json("POST", { preset }) : { method: "POST" }, tenantId);
}
```

`ThreatModelPage.tsx`:
- Imports: `import { costLabel, getModelCatalog, PRESET_LABELS, PRESETS, type ModelCatalog, type Preset } from "../../api/llmCatalog";` and `import { getRunPresets, type RunPresets } from "./threatModelApi";`.
- State: `const [runPresets, setRunPresets] = useState<RunPresets | null>(null);`, `const [catalog, setCatalog] = useState<ModelCatalog | null>(null);`, `const [preset, setPreset] = useState<Preset | "">("");`.
- Effect:

```tsx
  useEffect(() => {
    if (!tenantId) return;
    getRunPresets(tenantId, sessionId)
      .then((rp) => {
        setRunPresets(rp);
        // Prices only exist for OpenRouter's catalog.
        if (rp.credential_provider === "openrouter") return getModelCatalog(tenantId).then(setCatalog);
        return undefined;
      })
      .catch(() => setRunPresets(null));
  }, [sessionId, tenantId]);
```

- In `handleGenerate`: `triggerThreatModel(tenantId, sessionId, preset || undefined)`.
- In the header, immediately before the Generate `<button>` (inside the same conditional block, wrapped in a fragment):

```tsx
            {runPresets?.presets && (
              <select aria-label="Model preset" className="tm-preset" value={preset}
                onChange={(e) => setPreset(e.target.value as Preset | "")}>
                <option value="">Default model</option>
                {PRESETS.map((p) => {
                  const model = runPresets.presets![p];
                  const cost = costLabel(catalog, model);
                  return <option key={p} value={p}>{`${PRESET_LABELS[p]} · ${model}${cost ? ` · ${cost}` : ""}`}</option>;
                })}
              </select>
            )}
```

- Replace the empty-state `<p className="muted">…</p>` with:

```tsx
          <p className="muted">
            Set an LLM key in <Link to="/settings">Settings</Link> (or on this session from the extension), then click
            <strong> Generate Threat Model</strong> to analyse this session's recon surface.
          </p>
```

- [ ] **Step 4: Run to verify they pass.** `npm test -- --run`, `npm run lint`, `npm run build`.

- [ ] **Step 5: Commit**

```bash
git add apps/platform/web/src/api/apiClient.ts apps/platform/web/src/features/threat-model/
git commit -m "feat(web): pick a cost/strength preset when generating a threat model" -m "The Threat Model tab gets a Default/Cheapest/Balanced/Strongest select next to Generate. Each option names the model that preset will actually run for this session's credential (from /threat-model/presets), plus its cost per run when the credential is OpenRouter. The choice is sent as {preset}; Default sends nothing, so today's behaviour is unchanged. The empty-state copy now points to Settings instead of the extension.

Co-Authored-By: <the model you are>"
```

---

### Task 8: Docs + all four CI lanes

**Files:**
- Modify: `docs/OPERATING.md` (the `### Threat Model` subsection in §2)

- [ ] **Step 1: Docs.** At the end of the `### Threat Model` subsection, add:

```markdown
**Choosing a model.** Admins pick the team's default model in **Settings**. With an
OpenRouter key, **Choose from catalog…** lists OpenRouter's live models that can produce the
threat model's structured output, each with a cost per threat model (live price × this
team's average tokens per run, or an assumption until there's history). The three presets,
**Cheapest · Balanced · Strongest**, have built-in defaults per provider that an admin can
override (overrides reset when the team provider changes). Anyone can pick a preset next to
**Generate**; it applies to that run only and always uses the model for the provider of the
key that will run (a session's own key, else the team key, else the server key).
```

- [ ] **Step 2: Run all four lanes** (DoS timing guards only while Docker is idle):
  - host-tests (from `apps/platform`): `uv run ruff check src && uv run ruff format --check src && uv run mypy src/recon/findings src/recon/spec && RECON_REQUIRE_ENGINES=1 uv run pytest -m "not integration" --cov=recon --cov-fail-under=60`
  - frontend (from `apps/platform/web`): `npm ci && npm run lint && npm test -- --run && npm run build`
  - extension (from `apps/capture/chrome-extension`): `npm ci && npm run build && for t in tests/test_*.mjs; do node "$t" || echo FAIL $t; done`, then `git checkout -- dist`
  - integration: full suite in the isolated compose project, `pytest -m 'not dos_timing'` with `-e RECON_REQUIRE_ENGINES=1 -e RECON_AUTH_SECRET=""`, on an empty DB.
  Expected: all green, 0 failed.

- [ ] **Step 3: Commit**

```bash
git add docs/OPERATING.md
git commit -m "docs(operating): choosing the threat-model LLM model and presets" -m "Documents the catalog picker, the cost-per-threat-model estimate, the Cheapest/Balanced/Strongest presets with admin overrides, and per-run preset choice resolving against the running key's provider.

Co-Authored-By: <the model you are>"
```
