"""Threat model persistence: status flips, result storage and the read-model serialisation."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import object_session

from recon.db.base import tenant_session
from recon.db.models import SessionThreatModel, Threat
from recon.llm import calls
from recon.threat_model.schemas import ThreatItem, ThreatModelOutput


def link_llm_call(tenant_id: str, session_id: str) -> uuid.UUID:
    """Point this run at the LLM call it is about to make, so the UI shows THIS call's numbers
    (or "unavailable" if recording fails) — never a previous run's."""
    call_id = uuid.uuid4()
    with tenant_session(tenant_id) as db:
        db.execute(
            update(SessionThreatModel)
            .where(
                SessionThreatModel.session_id == uuid.UUID(session_id),
                SessionThreatModel.tenant_id == uuid.UUID(tenant_id),
            )
            .values(llm_call_id=call_id)
        )
    return call_id


def set_status(
    tenant_id: str,
    session_id: str,
    status: str,
    error: str | None = None,
) -> None:
    with tenant_session(tenant_id) as db:
        row = (
            db.query(SessionThreatModel)
            .filter_by(session_id=uuid.UUID(session_id), tenant_id=uuid.UUID(tenant_id))
            .first()
        )
        if row is None:
            return
        row.status = status
        row.error = error
        row.updated_at = dt.datetime.now(dt.UTC)
        db.flush()


def store_results(
    *,
    tenant_id: str,
    session_id: str,
    output: ThreatModelOutput,
    verified_threats: list[ThreatItem],
    provider: str,
    model: str,
    usage: Any,
) -> None:
    with tenant_session(tenant_id) as db:
        row = (
            db.query(SessionThreatModel)
            .filter_by(session_id=uuid.UUID(session_id), tenant_id=uuid.UUID(tenant_id))
            .first()
        )
        if row is None:
            return
        # Delete old threats atomically.
        for old in list(row.threats):
            db.delete(old)
        db.flush()

        row.status = "done"
        row.provider = provider
        row.model = model
        row.prompt_tokens = usage.prompt_tokens
        row.completion_tokens = usage.completion_tokens
        row.analysis_summary = output.analysis_summary
        row.error = None
        row.generated_at = dt.datetime.now(dt.UTC)
        row.updated_at = dt.datetime.now(dt.UTC)

        for rank, t in enumerate(verified_threats):
            threat = Threat(
                tenant_id=uuid.UUID(tenant_id),
                threat_model_id=row.id,
                rank=rank,
                title=t.title,
                owasp_category=t.owasp_category,
                severity=t.severity,
                description=t.description,
                affected_endpoints=t.affected_endpoints,
                test_steps=[s.model_dump() for s in t.test_steps],
                citations=t.citations,
            )
            db.add(threat)
        db.flush()


def serialize(row: SessionThreatModel, *, include_threats: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": row.status,
        "provider": row.provider,
        "model": row.model,
        "prompt_tokens": row.prompt_tokens,
        "completion_tokens": row.completion_tokens,
        "analysis_summary": row.analysis_summary,
        "error": row.error,
        "generated_at": row.generated_at.isoformat() if row.generated_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "last_call": _last_call(row),
    }
    if include_threats:
        result["threats"] = [_serialize_threat(t) for t in (row.threats or [])]
    return result


def _last_call(row: SessionThreatModel) -> dict[str, Any] | None:
    db = object_session(row)
    return calls.call_view(db, row.llm_call_id) if db is not None else None


def _serialize_threat(t: Threat) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "rank": t.rank,
        "title": t.title,
        "owasp_category": t.owasp_category,
        "severity": t.severity,
        "description": t.description,
        "affected_endpoints": t.affected_endpoints,
        "test_steps": t.test_steps,
        "citations": t.citations,
    }
