"""Extension capture lifecycle: ``capture/start`` and ``capture/end`` (design:
specs/2026-11-07-capture-lifecycle). A capture is one run from Start to End; ``analyze/start`` with a
``runId`` analyzes it without ending it (``capture_router.analyze_start``). An extension that never
calls ``capture/start`` keeps the older seal-on-analyze behaviour. Same tenant resolution and origin
lock as the rest of the ingest API."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from recon.api import capture_router as ingest
from recon.api.deps import get_redis
from recon.config import get_settings
from recon.db.base import tenant_session
from recon.db.models import Run
from recon.runs import capture_lifecycle
from recon.sessions import capture_scope

router = APIRouter(prefix="/api", tags=["capture"])


class CaptureStartIn(BaseModel):
    rootDomains: list[str] = Field(default_factory=list)  # noqa: N815 - extension wire name
    projectId: str | None = None  # noqa: N815


class ScopeAddIn(BaseModel):
    add: str


class CaptureEndIn(BaseModel):
    runId: str  # noqa: N815
    observations: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/sessions/{ext_session_id}/capture/start")
def capture_start(
    ext_session_id: str,
    body: CaptureStartIn,
    origin: str | None = Header(default=None, alias="Origin"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    ingest._enforce_origin_lock(origin, enabled=get_settings().capture_ingest_origin_lock)
    tenant_id, _ = ingest._resolve_ingest_tenant(authorization)
    domains = [d for d in body.rootDomains if isinstance(d, str) and d.strip()]
    session_id = ingest._get_or_create_session(
        tenant_id, ext_session_id, ingest._safe_uuid(body.projectId), domains
    )
    run_id = capture_lifecycle.start_capture(get_redis(), tenant_id, session_id, ext_session_id)
    return {"sessionId": session_id, "runId": run_id}


@router.post("/sessions/{ext_session_id}/capture/end")
def capture_end(
    ext_session_id: str,
    body: CaptureEndIn,
    origin: str | None = Header(default=None, alias="Origin"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    ingest._enforce_origin_lock(origin, enabled=get_settings().capture_ingest_origin_lock)
    tenant_id, _ = ingest._resolve_ingest_tenant(authorization)
    run_id = run_of_session(tenant_id, ext_session_id, body.runId)
    observed = ingest._normalize_observations(body.observations)
    return capture_lifecycle.end_capture(get_redis(), tenant_id, run_id, observations=observed)


@router.get("/capture/sessions")
def capture_sessions_for_scope(
    scope: str, authorization: str | None = Header(default=None, alias="Authorization")
) -> dict:
    """ "Continue acme.io": sessions whose scope holds ``scope``, with their largest capture.
    Signed-in only: a session's external id is a write capability for its captures, so it is
    never listed to an anonymous caller of the shared capture tenant."""
    tenant_id, signed_in = ingest._resolve_ingest_tenant(authorization)
    if not signed_in:
        raise HTTPException(status_code=401, detail="sign in to look up sessions")
    return {"sessions": [asdict(m) for m in capture_scope.sessions_for_scope(tenant_id, scope)]}


@router.post("/sessions/{ext_session_id}/scope")
def add_to_scope(
    ext_session_id: str,
    body: ScopeAddIn,
    origin: str | None = Header(default=None, alias="Origin"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    """ "Add to scope" on the popup's new-site badge."""
    ingest._enforce_origin_lock(origin, enabled=get_settings().capture_ingest_origin_lock)
    tenant_id, _ = ingest._resolve_ingest_tenant(authorization)
    session_id = ingest._find_session_by_external_id(tenant_id, ext_session_id)
    if session_id is None:
        raise HTTPException(status_code=404, detail="unknown capture session")
    try:
        scope = capture_scope.add_scope_entry(tenant_id, session_id, body.add)
    except capture_scope.InvalidScopeEntry as exc:
        raise HTTPException(status_code=422, detail="not a valid host") from exc
    return {"sessionId": session_id, "scope_hosts": scope}


def run_of_session(tenant_id: str, ext_session_id: str, run_id: str | None) -> str:
    """``run_id`` if it is a run of this extension session, else 404, so one session can never
    end or analyze another's capture."""
    session_id = ingest._find_session_by_external_id(tenant_id, ext_session_id)
    clean = ingest._safe_uuid(run_id)
    if session_id is None or clean is None:
        raise HTTPException(status_code=404, detail="unknown capture")
    with tenant_session(tenant_id) as session:
        owner = session.scalar(select(Run.session_id).where(Run.id == clean))
    if owner is None or str(owner) != str(session_id):
        raise HTTPException(status_code=404, detail="unknown capture")
    return clean
