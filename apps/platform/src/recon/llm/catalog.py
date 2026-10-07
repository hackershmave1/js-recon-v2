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
        # One malformed entry is skipped, not allowed to fail the whole catalog.
        if not isinstance(raw, dict):
            continue
        model_id = raw.get("id")
        if not isinstance(model_id, str) or model_id.endswith(":batch"):
            continue
        supported = raw.get("supported_parameters")
        if not isinstance(supported, list) or "response_format" not in supported:
            continue
        context = raw.get("context_length")
        if not isinstance(context, int) or context < _MIN_CONTEXT:
            continue
        top_provider, pricing = raw.get("top_provider", {}), raw.get("pricing", {})
        # A null top_provider means "no output cap published"; any other non-dict is junk.
        top_provider = {} if top_provider is None else top_provider
        if not isinstance(top_provider, dict) or not isinstance(pricing, dict):
            continue
        max_out = top_provider.get("max_completion_tokens")
        if isinstance(max_out, int) and max_out < _MIN_OUTPUT_TOKENS:
            continue
        prompt, completion = _price(pricing.get("prompt")), _price(pricing.get("completion"))
        if prompt is None or completion is None:
            continue
        kept.append(
            CatalogModel(
                id=model_id,
                name=str(raw.get("name") or model_id),
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


async def get_catalog(
    client_factory: Callable[[], httpx.AsyncClient] | None = None,
) -> dict[str, Any]:
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
        # A malformed 200 body (list top level, non-dict entries) is a failed fetch too.
        except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
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
