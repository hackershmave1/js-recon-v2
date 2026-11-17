"""Threat model generation — context assembly, LLM call, citation verification, storage.

Flow:
1. ``trigger_generation`` upserts a ``session_threat_model`` row with status="pending"
   and returns it. The router starts the actual generation in a FastAPI BackgroundTask.
2. ``run_generation`` does the heavy work: assembles context from the session's latest
   run, calls the configured LLM provider, verifies citations (REQ-L4), then stores
   threats and flips status to "done". On any failure, status flips to "failed".
3. ``get_threat_model`` reads the current state + threats for the session.

Citation verification (REQ-L4): every finding_hash cited by the LLM is checked against
the set of hashes in the session's most-recent run. Citations for hashes that don't
exist in the run are silently dropped rather than blocking the whole result.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import update

from recon.db.base import tenant_session
from recon.db.models import EngagementSession, SessionThreatModel
from recon.llm import calls, run_choice
from recon.llm.crypto import KeyDecryptError
from recon.observability import get_logger
from recon.sessions import service as sessions_service
from recon.threat_model import store
from recon.threat_model.context import _assemble_context, _Context
from recon.threat_model.prompt import SYSTEM_PROMPT
from recon.threat_model.schemas import ThreatItem, ThreatModelOutput

__all__ = [
    "ThreatItem",
    "ThreatModelOutput",
    "_Context",
    "_assemble_context",
    "get_threat_model",
    "run_generation",
    "trigger_generation",
]

log = get_logger("recon.threat_model")

# ---------------------------------------------------------------------------
# Session ID resolution (mirrors sessions_router / llm.service pattern)
# ---------------------------------------------------------------------------


def _resolve_session_id(tenant_id: str, session_id: str) -> str:
    """Accept platform UUID or extension external_id. Returns platform UUID or raises."""
    try:
        uuid.UUID(session_id)  # validate format
        with tenant_session(tenant_id) as db:
            row = db.query(EngagementSession).filter_by(id=uuid.UUID(session_id)).first()
            if row is not None:
                return session_id
    except (ValueError, AttributeError):
        pass
    platform_id = sessions_service.find_session_id_by_external_id(tenant_id, session_id)
    if platform_id is None:
        raise ValueError("session not found")
    return platform_id


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------


def trigger_generation(tenant_id: str, session_id: str) -> dict[str, Any]:
    """Upsert a pending threat model record for the session. Returns the serialised state.

    If a generation is already running or pending, returns the current state unchanged
    (idempotent). If a previous result exists (done or failed), resets it so a fresh
    generation will overwrite the threats."""
    try:
        resolved = _resolve_session_id(tenant_id, session_id)
    except ValueError:
        return {}

    with tenant_session(tenant_id) as db:
        existing = (
            db.query(SessionThreatModel)
            .filter_by(session_id=uuid.UUID(resolved), tenant_id=uuid.UUID(tenant_id))
            .first()
        )
        if existing:
            if existing.status == "pending":
                return store.serialize(existing)
            if existing.status == "running":
                # Only block if the row was updated within the last 5 minutes — otherwise
                # treat it as orphaned by a server restart and allow re-trigger.
                age = (dt.datetime.now(dt.UTC) - existing.updated_at).total_seconds()
                if age < 300:
                    return store.serialize(existing)
            # Re-trigger: reset status, clear old result.
            existing.status = "pending"
            existing.error = None
            existing.analysis_summary = None
            existing.generated_at = None
            existing.llm_call_id = None  # the UI must not show the previous run's numbers
            existing.updated_at = dt.datetime.now(dt.UTC)
            db.flush()
            db.refresh(existing)
            return store.serialize(existing)

        row = SessionThreatModel(
            tenant_id=uuid.UUID(tenant_id),
            session_id=uuid.UUID(resolved),
            status="pending",
        )
        db.add(row)
        db.flush()
        db.refresh(row)
        return store.serialize(row)


def get_threat_model(tenant_id: str, session_id: str) -> dict[str, Any] | None:
    """Return current threat model state + threats, or None if none exists."""
    try:
        resolved = _resolve_session_id(tenant_id, session_id)
    except ValueError:
        return None

    with tenant_session(tenant_id) as db:
        row = (
            db.query(SessionThreatModel)
            .filter_by(session_id=uuid.UUID(resolved), tenant_id=uuid.UUID(tenant_id))
            .first()
        )
        if row is None:
            return None
        return store.serialize(row, include_threats=True)


async def run_generation(tenant_id: str, session_id: str, preset: str | None = None) -> None:
    """Background task: assemble context → call LLM → verify citations → store threats.

    ``preset`` (cheapest/balanced/strongest) picks the model for this run from the
    credential's own provider; None keeps the saved model."""
    try:
        resolved = _resolve_session_id(tenant_id, session_id)
    except ValueError:
        log.warning("threat_model.session_not_found", session_id=session_id)
        return

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

    try:
        context = _assemble_context(tenant_id, resolved)
    except ValueError as exc:
        store.set_status(tenant_id, resolved, "failed", error=str(exc))
        log.warning("threat_model.context_failed", session_id=resolved, error=str(exc))
        return

    try:
        # Inside the try: a failure here must mark the run failed, not leave it running.
        choice = run_choice.resolve(tenant_id, resolved, preset, with_key=True)
    except Exception as exc:
        # NOTE: this runs after status=running; an escaped exception would leave the
        # model stuck there until the 5-minute orphan window, for every session of a
        # team with a bad team key.
        reason = (
            "stored LLM key could not be decrypted; re-save it"
            if isinstance(exc, KeyDecryptError)
            else "could not load LLM credentials"
        )
        store.set_status(tenant_id, resolved, "failed", error=reason)
        if isinstance(exc, KeyDecryptError):
            # No traceback here: the decrypt frames could carry ciphertext.
            log.error(
                "llm.credentials.decrypt_failed",
                tenant_id=tenant_id,
                session_id=resolved,
                error_type=type(exc).__name__,
            )
        else:
            log.error(
                "llm.credentials.load_failed",
                tenant_id=tenant_id,
                session_id=resolved,
                error_type=type(exc).__name__,
                exc_info=True,
            )
        return
    if choice is None:
        store.set_status(
            tenant_id,
            resolved,
            "failed",
            error="no LLM API key: save one for this session, ask an admin to set a team "
            "key in Settings, or set OPENROUTER_API_KEY / ANTHROPIC_API_KEY on the server",
        )
        return
    log.info(
        "threat_model.generation_model",
        tenant_id=tenant_id,
        session_id=resolved,
        preset=preset,
        provider=choice.provider,
        model=choice.model,
        key_source=choice.source,
    )

    try:
        call_id = store.link_llm_call(tenant_id, resolved)
        response = await calls.generate(
            choice,
            tenant_id=tenant_id,
            purpose="threat_model",
            system_prompt=SYSTEM_PROMPT,
            user_prompt=context.markdown,
            output_schema=ThreatModelOutput,
            max_tokens=12000,
            session_id=resolved,
            run_id=context.run_id,
            call_id=call_id,
        )
        output = ThreatModelOutput.model_validate(response.content)
    except Exception as exc:
        store.set_status(tenant_id, resolved, "failed", error=str(exc))
        log.error("threat_model.llm_failed", session_id=resolved, error=str(exc))
        return

    # REQ-L4 citation verification: drop hashes that don't exist in the run.
    verified_threats = []
    for threat_item in output.threats:
        valid_citations = [h for h in threat_item.citations if h in context.valid_hashes]
        verified_threats.append(
            ThreatItem(
                title=threat_item.title,
                owasp_category=threat_item.owasp_category,
                severity=threat_item.severity,
                description=threat_item.description,
                affected_endpoints=threat_item.affected_endpoints,
                test_steps=threat_item.test_steps,
                citations=valid_citations,
            )
        )

    store.store_results(
        tenant_id=tenant_id,
        session_id=resolved,
        output=output,
        verified_threats=verified_threats,
        provider=choice.provider,
        model=response.model,
        usage=response.usage,
    )
    log.info(
        "threat_model.done",
        session_id=resolved,
        threats=len(verified_threats),
        prompt_tokens=response.usage.prompt_tokens,
        completion_tokens=response.usage.completion_tokens,
    )
