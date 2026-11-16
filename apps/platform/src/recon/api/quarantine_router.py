"""Quarantine endpoints (ADR-0010): the extension's ingest of an undecided host's scripts, and the
researcher's undecided-host list and Add / Ignore decision. API only: the popup (S2) and the Hosts
page (S3) are built on these.

* ``POST /api/quarantine/files``: ingest, like ``/api/save-files`` (Origin lock, ingest tenant),
  for the session's OPEN capture only. Replays after Ignore store nothing.
* ``GET  /sessions/{id}/hosts/undecided`` and ``/hosts/ignored``: the lists.
* ``POST /sessions/{id}/hosts/{host}/decision``: ``{"decision": "add" | "ignore"}``, a
  compare-and-set: 200 with the new state, 409 when another decision already won.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from redis import Redis
from sqlalchemy import select

from recon.api import capture_router
from recon.api.deps import get_redis, get_tenant_id
from recon.config import get_settings
from recon.db.base import tenant_session
from recon.db.models import Run
from recon.observability import get_logger
from recon.quarantine import service
from recon.runs import capture_lifecycle, coordinator

log = get_logger("recon.quarantine")

router = APIRouter(tags=["quarantine"])

_MAX_BATCH_FILES = 20


class QuarantineFilesIn(BaseModel):
    sessionId: str  # the extension's session id (external_id), as in save-files metadata
    runId: str
    host: str
    # Lenient like save-files: validated per file in the handler.
    files: list[dict[str, Any]] = Field(default_factory=list)


class DecisionIn(BaseModel):
    decision: Literal["add", "ignore"]


@router.post("/api/quarantine/files")
def ingest_quarantine_files(
    payload: QuarantineFilesIn,
    origin: str | None = Header(default=None, alias="Origin"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    settings = get_settings()
    capture_router._enforce_origin_lock(origin, enabled=settings.capture_ingest_origin_lock)
    tenant_id, _paired = capture_router._resolve_ingest_tenant(authorization)
    if len(payload.files) > _MAX_BATCH_FILES:
        raise HTTPException(status_code=413, detail="too many files in one batch")
    # Only the session's OPEN capture takes quarantined files, so a late or forged batch for an
    # ended one stores nothing (the same rule as save-files).
    if capture_lifecycle.find_open_run(tenant_id, payload.sessionId) != payload.runId:
        raise HTTPException(status_code=409, detail="capture_ended")
    with tenant_session(tenant_id) as session:
        platform_session = session.scalar(select(Run.session_id).where(Run.id == payload.runId))
    if platform_session is None:
        raise HTTPException(status_code=404, detail="run not found")
    files: list[service.FileIn] = []
    for item in payload.files:
        url, content = item.get("url"), item.get("content")
        if isinstance(url, str) and isinstance(content, str) and url:
            files.append(service.FileIn(url=url, data=content.encode("utf-8")))
    try:
        results = service.admit(
            tenant_id=tenant_id,
            session_id=str(platform_session),
            run_id=payload.runId,
            host=payload.host,
            files=files,
        )
    except service.QuarantineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "success": True,
        "files": [{"url": r.url, "outcome": r.outcome} for r in results],
    }


@router.get("/sessions/{session_id}/hosts/undecided")
def list_undecided(session_id: str, tenant_id: str = Depends(get_tenant_id)) -> dict:
    return {"hosts": service.undecided_hosts(tenant_id=tenant_id, session_id=session_id)}


@router.get("/sessions/{session_id}/hosts/ignored")
def list_ignored(session_id: str, tenant_id: str = Depends(get_tenant_id)) -> dict:
    return {"hosts": service.ignored_hosts(tenant_id=tenant_id, session_id=session_id)}


@router.post("/sessions/{session_id}/hosts/{host}/decision")
def decide_host(
    session_id: str,
    host: str,
    body: DecisionIn,
    tenant_id: str = Depends(get_tenant_id),
    redis: Redis = Depends(get_redis),
) -> dict:
    try:
        result = service.decide(
            tenant_id=tenant_id, session_id=session_id, host=host, decision=body.decision
        )
    except service.QuarantineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result.status == "not_found":
        raise HTTPException(status_code=404, detail="no undecided host")
    if result.status == "conflict":
        raise HTTPException(
            status_code=409, detail={"error": "already_decided", "state": result.state}
        )
    return {
        "state": result.state,
        "promoted": result.promoted,
        "deleted": result.deleted,
        "analysis": result.analysis,
        "recrawl": _start_recrawl(redis, tenant_id, result.recrawl_run_id),
    }


def _start_recrawl(redis: Redis, tenant_id: str, source_run_id: str | None) -> dict | None:
    """K1: Add of a host a crawl only REFERENCED. The host is now in the scope, so one new crawl of
    the same target fetches its scripts through the unchanged egress guard (the "fetch pass").
    The Add already happened and stands: a failure here is reported, never turned into an error."""
    if source_run_id is None:
        return None
    try:
        view = coordinator.edit_and_rerun(redis, tenant_id=tenant_id, run_id=source_run_id)
    except Exception as exc:  # noqa: BLE001 - Add is committed; the researcher can re-run by hand
        log.exception("quarantine.recrawl_failed", run_id=source_run_id)
        return {"source_run_id": source_run_id, "run_id": None, "error": type(exc).__name__}
    return {"source_run_id": source_run_id, "run_id": view.id}
