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
from typing import Any, NamedTuple

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import object_session

from recon.db.base import tenant_session
from recon.db.models import (
    EngagementSession,
    Finding,
    Run,
    RunTechnology,
    SessionSpec,
    SessionThreatModel,
    Threat,
)
from recon.domain import FindingType, RunState
from recon.findings import normalize, visibility
from recon.llm import calls, run_choice
from recon.llm.crypto import KeyDecryptError
from recon.observability import get_logger
from recon.sessions import service as sessions_service

log = get_logger("recon.threat_model")

_TERMINAL_STATES = frozenset(
    {RunState.DONE.value, RunState.PARTIAL.value, RunState.FAILED.value, RunState.CANCELLED.value}
)

# Finding types that feed the threat model context.
_ENDPOINT_TYPES = frozenset({FindingType.ENDPOINT.value, FindingType.ENDPOINT_SUSPECTED.value})
_SECRET_TYPES = frozenset({FindingType.SECRET.value, FindingType.SECRET_SUSPECTED.value})
_INFO_TYPES = frozenset(
    {
        FindingType.GRAPHQL.value,
        FindingType.INTERNAL_IP.value,
        FindingType.POSTMESSAGE_SINK.value,
        FindingType.STORAGE_SINK.value,
    }
)


# ---------------------------------------------------------------------------
# Pydantic schemas — passed to generate_structured as the output_schema
# ---------------------------------------------------------------------------


class TestStep(BaseModel):
    model_config = {"extra": "ignore"}
    action: str = ""
    tool: str = ""
    command: str = ""
    expected_if_vulnerable: str = ""
    expected_if_secure: str = ""


class ThreatItem(BaseModel):
    model_config = {"extra": "ignore"}
    title: str
    owasp_category: str = ""
    severity: str = "medium"
    description: str = ""
    affected_endpoints: list[str] = []
    test_steps: list[TestStep] = []
    citations: list[str] = []


class ThreatModelOutput(BaseModel):
    model_config = {"extra": "ignore"}
    analysis_summary: str = ""
    threats: list[ThreatItem] = []


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
# Context assembly
# ---------------------------------------------------------------------------


class _Context(NamedTuple):
    markdown: str
    valid_hashes: frozenset[str]
    run_id: str  # the run the prompt describes; the LLM call's bodies are stored under it


def _assemble_context(tenant_id: str, session_id: str) -> _Context:
    """Build the LLM prompt context from the session's latest completed run.

    Returns (context_markdown, valid_finding_hashes). Raises ValueError if no
    terminal run exists for the session yet.
    """
    with tenant_session(tenant_id) as db:
        # Session metadata.
        session_row = db.get(EngagementSession, session_id)
        session_name = (session_row.name or session_id[:8]) if session_row else session_id[:8]
        scope_hosts: list[str] = (session_row.scope_hosts or []) if session_row else []

        # Latest terminal run. Run.id is UUID v4 (random) — sort by created_at.
        run_row = db.execute(
            select(Run.id)
            .where(
                Run.session_id == session_id,
                Run.state.in_(_TERMINAL_STATES),
            )
            .order_by(Run.created_at.desc())
            .limit(1)
        ).first()
        if run_row is None:
            raise ValueError("no completed run for this session")
        run_id = str(run_row[0])

        # The run's SHOWN findings — grouped by type. Hidden ones (analytics noise, junk /
        # off-scope page routes — findings/visibility) are not the target's surface.
        hidden = visibility.hidden_finding_ids(db, run_id)
        finding_rows = [
            (ftype, fvalue, fhash, fattrs)
            for fid, ftype, fvalue, fhash, fattrs in db.execute(
                select(
                    Finding.id,
                    Finding.type,
                    Finding.value,
                    Finding.finding_hash,
                    Finding.attributes,
                )
                .where(Finding.run_id == run_id)
                .order_by(Finding.type, Finding.value)
            ).all()
            if str(fid) not in hidden
        ]

        endpoints: list[str] = []
        secret_type_counts: dict[str, int] = {}
        graphql_ops: list[str] = []
        info_findings: dict[str, list[str]] = {}
        valid_hashes: set[str] = set()

        for ftype, fvalue, fhash, fattrs in finding_rows:
            valid_hashes.add(fhash)
            if ftype in _ENDPOINT_TYPES:
                method = ""
                if isinstance(fattrs, dict):
                    m = fattrs.get("method") or (
                        ", ".join(fattrs.get("methods", [])) if fattrs.get("methods") else ""
                    )
                    method = f"[{m}] " if m else ""
                # Display value: `{id}` named from the observed identifier (`{userId}`), so the
                # model keeps the hint a developer's name carries (identity v3).
                shown = normalize.display_endpoint_value(
                    fvalue, (fattrs or {}).get("path_params") if isinstance(fattrs, dict) else None
                )
                endpoints.append(f"{method}{shown}")
            elif ftype in _SECRET_TYPES:
                # REQ-S2: never surface raw secret values. Count by provider prefix.
                provider_prefix = fvalue.split(":")[0] if ":" in fvalue else ftype
                secret_type_counts[provider_prefix] = secret_type_counts.get(provider_prefix, 0) + 1
            elif ftype == FindingType.GRAPHQL.value:
                op_name = (fattrs or {}).get("operation_name", fvalue) if fattrs else fvalue
                graphql_ops.append(str(op_name))
            elif ftype in _INFO_TYPES:
                info_findings.setdefault(ftype, []).append(fvalue)

        # Technologies.
        tech_rows = db.execute(
            select(RunTechnology.name, RunTechnology.version, RunTechnology.categories)
            .where(RunTechnology.run_id == run_id)
            .order_by(RunTechnology.name)
        ).all()
        techs = [f"{name} {version}" if version else name for name, version, _ in tech_rows]

        # OpenAPI spec summary.
        spec_row = db.execute(
            select(
                SessionSpec.operation_count, SessionSpec.server_bases, SessionSpec.spec_format
            ).where(SessionSpec.session_id == session_id)
        ).first()

    # Build the markdown context.
    lines: list[str] = [
        f"# Recon Surface: {session_name}",
        "",
        f"**In-scope hosts:** {', '.join(scope_hosts) or '(not specified)'}",
        "",
    ]

    lines += [f"## API Endpoints ({len(endpoints)} total)"]
    if endpoints:
        for ep in endpoints[:500]:  # cap to avoid context explosion
            lines.append(f"- {ep}")
        if len(endpoints) > 500:
            lines.append(f"- ... and {len(endpoints) - 500} more")
    else:
        lines.append("No endpoints discovered.")
    lines.append("")

    total_secrets = sum(secret_type_counts.values())
    lines += [f"## Secrets Detected ({total_secrets} total — values redacted)"]
    if secret_type_counts:
        for stype, count in sorted(secret_type_counts.items(), key=lambda x: -x[1]):
            lines.append(f"- {stype}: {count}")
    else:
        lines.append("No secrets detected.")
    lines.append("")

    if graphql_ops:
        lines += [f"## GraphQL Operations ({len(graphql_ops)})"]
        for op in graphql_ops[:50]:
            lines.append(f"- {op}")
        lines.append("")

    if techs:
        lines += [f"## Technologies Detected ({len(techs)})"]
        for t in techs:
            lines.append(f"- {t}")
        lines.append("")

    for ftype, vals in info_findings.items():
        label = {
            FindingType.INTERNAL_IP.value: "Internal IP Literals",
            FindingType.POSTMESSAGE_SINK.value: "postMessage Sinks",
            FindingType.STORAGE_SINK.value: "Web Storage / Cookie Sinks",
        }.get(ftype, ftype)
        lines += [f"## {label} ({len(vals)})"]
        for v in vals[:30]:
            lines.append(f"- {v}")
        lines.append("")

    if spec_row:
        op_count, server_bases, spec_format = spec_row
        lines += [
            "## OpenAPI Spec",
            f"Format: {spec_format} · {op_count} operations · "
            f"Bases: {', '.join(server_bases) if server_bases else '(none)'}",
            "",
        ]
    else:
        lines += ["## OpenAPI Spec", "No spec uploaded for this session.", ""]

    return _Context("\n".join(lines), frozenset(valid_hashes), run_id)


_SYSTEM_PROMPT = """\
You are an Expert Bug Bounty Engineer performing a threat model review on a JavaScript SPA.
You have been given the static recon surface extracted from the application's JS bundles:
API endpoints, secrets detected (counts only — values redacted), technologies, and scope.

Your job: identify concrete, testable security threats from this surface.

Output ONLY a valid JSON object with exactly this structure — no prose, no markdown, no code fences:

{
  "analysis_summary": "2-3 sentence summary of the attack surface and risk posture",
  "threats": [
    {
      "title": "Short threat title (max 80 chars)",
      "owasp_category": "A01:2021",
      "severity": "critical|high|medium|low|info",
      "description": "1-2 sentence description referencing specific endpoints or findings",
      "affected_endpoints": ["/api/endpoint1", "/api/endpoint2"],
      "test_steps": [
        {
          "action": "what to do",
          "tool": "burp|curl|browser|custom",
          "command": "exact curl command or Burp payload",
          "expected_if_vulnerable": "what you see if vulnerable",
          "expected_if_secure": "what you see if secure"
        }
      ],
      "citations": []
    }
  ]
}

Rules:
- owasp_category: use OWASP Top 10 2021 codes A01:2021 through A10:2021, or "Other".
- severity: must be exactly one of: critical, high, medium, low, info.
- citations: leave as empty array [] — citation verification is done server-side.
- Each threat MUST reference specific endpoints from the provided surface.
- Output at most 12 threats — prioritise by severity (critical first).
- Each threat has at most 3 test_steps.
- description: max 2 sentences. command: max 200 chars. Keep all strings concise.
- Output ONLY the JSON object — no other text, no markdown, no code fences.
"""


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
                return _serialize(existing)
            if existing.status == "running":
                # Only block if the row was updated within the last 5 minutes — otherwise
                # treat it as orphaned by a server restart and allow re-trigger.
                age = (dt.datetime.now(dt.UTC) - existing.updated_at).total_seconds()
                if age < 300:
                    return _serialize(existing)
            # Re-trigger: reset status, clear old result.
            existing.status = "pending"
            existing.error = None
            existing.analysis_summary = None
            existing.generated_at = None
            existing.llm_call_id = None  # the UI must not show the previous run's numbers
            existing.updated_at = dt.datetime.now(dt.UTC)
            db.flush()
            db.refresh(existing)
            return _serialize(existing)

        row = SessionThreatModel(
            tenant_id=uuid.UUID(tenant_id),
            session_id=uuid.UUID(resolved),
            status="pending",
        )
        db.add(row)
        db.flush()
        db.refresh(row)
        return _serialize(row)


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
        return _serialize(row, include_threats=True)


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
        _set_status(tenant_id, resolved, "failed", error=str(exc))
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
        _set_status(tenant_id, resolved, "failed", error=reason)
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
        _set_status(
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
        call_id = _link_llm_call(tenant_id, resolved)
        response = await calls.generate(
            choice,
            tenant_id=tenant_id,
            purpose="threat_model",
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=context.markdown,
            output_schema=ThreatModelOutput,
            max_tokens=12000,
            session_id=resolved,
            run_id=context.run_id,
            call_id=call_id,
        )
        output = ThreatModelOutput.model_validate(response.content)
    except Exception as exc:
        _set_status(tenant_id, resolved, "failed", error=str(exc))
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

    _store_results(
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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _link_llm_call(tenant_id: str, session_id: str) -> uuid.UUID:
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


def _set_status(
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


def _store_results(
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


def _serialize(row: SessionThreatModel, *, include_threats: bool = False) -> dict[str, Any]:
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
