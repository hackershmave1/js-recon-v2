"""Every LLM API call goes through ``generate``: it builds the provider adapter, times the call,
and records what happened (CLAUDE.md section 5) — model, whose key paid, tokens, duration,
cost, finish reason, and (when the call belongs to a run) the full prompt and response as
run-scoped blobs, so prompts can be replayed when evaluating a new system prompt.

Recording is best-effort: it runs in a worker thread (S3 + DB are blocking) and a failure only
logs ``llm.call.record_failed`` — it never costs the caller its result.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy.orm import Session

from recon import storage
from recon.db.base import tenant_session
from recon.db.models import LlmCall
from recon.llm import catalog
from recon.llm.provider import DEFAULT_MODELS, LLMResponse, LLMUsage, build_provider
from recon.llm.run_choice import RunChoice
from recon.observability import get_logger

log = get_logger(__name__)

Purpose = Literal["threat_model", "key_test"]

# Stored error text is capped; provider errors can embed long request echoes.
_ERROR_MAX = 2000


@dataclass(frozen=True)
class _CallRecord:
    call_id: uuid.UUID
    tenant_id: str
    purpose: Purpose
    session_id: str | None
    run_id: str | None
    choice: RunChoice
    model: str
    latency_ms: int
    status: Literal["ok", "error"]
    usage: LLMUsage | None
    finish_reason: str | None
    error: str | None
    prompt_body: str
    response_body: str  # the response JSON, or the (redacted) error text

    @property
    def cost_usd(self) -> Decimal | None:
        """USD from the already-cached catalog price (OpenRouter only); None = unknown."""
        if self.choice.provider != "openrouter" or self.usage is None or not self.usage.reported:
            return None
        price = catalog.cached_price(self.model)
        if price is None:
            return None
        prompt_price, completion_price = price
        return (
            prompt_price * self.usage.prompt_tokens
            + completion_price * self.usage.completion_tokens
        )


async def generate(
    choice: RunChoice,
    *,
    tenant_id: str,
    purpose: Purpose,
    system_prompt: str,
    user_prompt: str,
    output_schema: type[BaseModel],
    max_tokens: int,
    session_id: str | None = None,
    run_id: str | None = None,
    call_id: uuid.UUID | None = None,
) -> LLMResponse:
    """Make one structured LLM call and record it. Returns the response or re-raises the
    provider's own exception unchanged. "ok" means the provider answered with a usable payload;
    a caller's own validation of that payload (e.g. the threat-model schema) is not part of the
    record. ``call_id`` lets a caller link the record before the
    call starts (the threat model stores it so the UI shows this run's numbers, not a past one).
    Prompt/response bodies are stored only with a ``run_id`` (they are purged with the run)."""
    base: dict[str, Any] = {
        "call_id": call_id or uuid.uuid4(),
        "tenant_id": tenant_id,
        "purpose": purpose,
        "session_id": session_id,
        "run_id": run_id,
        "choice": choice,
        "model": choice.model or DEFAULT_MODELS.get(choice.provider, ""),
        "prompt_body": json.dumps(
            {
                "system": system_prompt,
                "user": user_prompt,
                "schema": output_schema.__name__,
                "max_tokens": max_tokens,
            }
        ),
    }
    started = time.monotonic()
    try:
        adapter = build_provider(choice.provider, api_key=choice.api_key or "", model=choice.model)
        base["model"] = adapter.model
        response = await adapter.generate_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            output_schema=output_schema,
            max_tokens=max_tokens,
        )
    # NOTE: BaseException (task cancelled, shutdown) is deliberately not recorded: there is no
    # outcome to report, and a linked caller then shows "Run details unavailable", which is true.
    except Exception as exc:
        error = _redact(str(exc) or type(exc).__name__, choice.api_key)
        await _record_safely(
            _CallRecord(
                **base,
                latency_ms=_elapsed_ms(started),
                status="error",
                usage=getattr(exc, "usage", None),  # LLMOutputError: the provider did answer
                finish_reason=getattr(exc, "finish_reason", None),
                error=error,
                response_body=error,
            )
        )
        raise
    await _record_safely(
        _CallRecord(
            **base,
            latency_ms=_elapsed_ms(started),
            status="ok",
            usage=response.usage,
            finish_reason=response.finish_reason,
            error=None,
            response_body=json.dumps(response.content),
        )
    )
    return response


def call_view(db: Session, call_id: uuid.UUID | None) -> dict[str, Any] | None:
    """The run-details line for the UI. None = no call was made; ``unavailable`` = a call was
    made but its record couldn't be saved."""
    if call_id is None:
        return None
    row = db.get(LlmCall, call_id)
    if row is None:
        return {"unavailable": True}
    return {
        "unavailable": False,
        "provider": row.provider,
        "model": row.model,
        "key_source": row.key_source,
        "status": row.status,
        "prompt_tokens": row.prompt_tokens,
        "completion_tokens": row.completion_tokens,
        "cost_usd": str(row.cost_usd) if row.cost_usd is not None else None,
        "latency_ms": row.latency_ms,
        "finish_reason": row.finish_reason,
    }


async def _record_safely(rec: _CallRecord) -> None:
    cost = rec.cost_usd
    try:
        await asyncio.to_thread(_record, rec, cost)
    except Exception as exc:
        log.warning(
            "llm.call.record_failed",
            tenant_id=rec.tenant_id,
            purpose=rec.purpose,
            call_id=str(rec.call_id),
            error_type=type(exc).__name__,
        )
    log.info(
        "llm.call",
        tenant_id=rec.tenant_id,
        session_id=rec.session_id,
        purpose=rec.purpose,
        call_id=str(rec.call_id),
        provider=rec.choice.provider,
        model=rec.model,
        key_source=rec.choice.source,
        status=rec.status,
        finish_reason=rec.finish_reason,
        latency_ms=rec.latency_ms,
        prompt_tokens=rec.usage.prompt_tokens if rec.usage else None,
        completion_tokens=rec.usage.completion_tokens if rec.usage else None,
        cost_usd=str(cost) if cost is not None else None,
    )


def _record(rec: _CallRecord, cost: Decimal | None) -> None:
    # Row first, blobs second: if the session was deleted meanwhile, the insert fails on its FK
    # and no blob is written behind the session purge's back (residual window: DEBT D72).
    with tenant_session(rec.tenant_id) as db:
        db.add(
            LlmCall(
                id=rec.call_id,
                tenant_id=uuid.UUID(rec.tenant_id),
                session_id=uuid.UUID(rec.session_id) if rec.session_id else None,
                run_id=uuid.UUID(rec.run_id) if rec.run_id else None,
                purpose=rec.purpose,
                provider=rec.choice.provider,
                model=rec.model,
                key_source=rec.choice.source,
                status=rec.status,
                error=rec.error,
                finish_reason=rec.finish_reason,
                prompt_tokens=rec.usage.prompt_tokens if _reported(rec.usage) else None,
                completion_tokens=rec.usage.completion_tokens if _reported(rec.usage) else None,
                latency_ms=rec.latency_ms,
                cost_usd=cost,
            )
        )
    if rec.run_id is None:
        return
    prompt_key = storage.put_blob(rec.tenant_id, rec.run_id, "llm-prompt", rec.prompt_body.encode())
    response_key = storage.put_blob(
        rec.tenant_id, rec.run_id, "llm-response", rec.response_body.encode()
    )
    with tenant_session(rec.tenant_id) as db:
        row = db.get(LlmCall, rec.call_id)
        if row is not None:
            row.prompt_blob_key = prompt_key
            row.response_blob_key = response_key


def _reported(usage: LLMUsage | None) -> bool:
    return usage is not None and usage.reported


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def _redact(text: str, api_key: str | None) -> str:
    # SDK error strings are not guaranteed to omit the key; it must never reach storage.
    if api_key:
        text = text.replace(api_key, "[redacted]")
    return text[:_ERROR_MAX]
