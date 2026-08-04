"""SPIKE: extension -> platform ingest bridge (throwaway, flag-gated).

Accepts the Chrome extension's batched ``POST /api/save-files`` payload and fans
it into the platform's EXISTING run->analyze machinery — one platform ``Run`` per
file — running analyze SYNCHRONOUSLY in-process (no Redis worker/queue). Its whole
purpose is to MEASURE how deeply the platform's analysis couples to Redis /
S3-MinIO / multi-tenant RLS. Mounted only when ``settings.enable_capture_ingest``
is true (see ``api/app.py``, which also swaps blob storage to local disk).

Deliberate spike shortcuts, documented so they aren't mistaken for real design:
- One ``Run`` per file (``Run.input_ref`` is singular). A real bridge would batch
  N files into one run via ``run_asset`` rows (the Slice-Y multi-asset path).
- ``run.state`` stays ``"queued"``: we skip the worker's 5-stage walk.
  ``list_findings`` ignores ``run.state`` so findings still read back — the state
  is cosmetic here, not a bug.
- No ``X-Tenant-Id`` header: a single ``capture-spike`` tenant is get-or-created.
  No auth. This is a single-user local seam, not the multi-tenant contract.
- Never returns 4xx for a per-file failure: the extension DROPS a whole batch on
  any 4xx (non-429), so a bad file would silently lose un-recapturable post-auth
  JS. Failures are recorded per file and the batch still returns 200.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from recon import storage
from recon.api.deps import get_redis
from recon.config import get_settings
from recon.db.base import admin_session, tenant_session
from recon.db.models import EngagementSession, Run, Tenant
from recon.findings import analyze
from recon.observability import get_logger
from recon.runs import service as runs_service
from recon.sessions import service as sessions_service

log = get_logger("recon.api.capture")

router = APIRouter(prefix="/api", tags=["capture-spike"])


class CaptureFileIn(BaseModel):
    model_config = {"extra": "allow"}  # tolerate the extension's extra keys
    url: str
    contentHash: str
    sessionId: str
    content: str
    contentLength: int | None = None
    sourceMapUrl: str | None = None
    sourceMapContent: dict | None = None
    headers: dict[str, str] | None = None
    dependencies: list[dict] = Field(default_factory=list)


class SaveFilesIn(BaseModel):
    metadata: dict[str, Any] = Field(default_factory=dict)
    files: list[CaptureFileIn]


@router.get("/health")
def capture_health() -> dict:
    """Mirror the extension's workspace-client health probe."""
    return {"status": "ok", "mode": "platform-spike"}


def _get_or_create_tenant(name: str) -> str:
    with admin_session() as session:
        existing = session.scalar(select(Tenant).where(Tenant.name == name))
        if existing is not None:
            return str(existing.id)
    # No row yet — create_tenant opens its own admin_session.
    return sessions_service.create_tenant(name)


def _get_or_create_session(tenant_id: str, ext_session_id: str) -> str:
    # Map the extension's sessionId -> a platform session idempotently, keyed by
    # name, so a retried batch reuses the session instead of piling up duplicates.
    with tenant_session(tenant_id) as session:
        existing = session.scalar(
            select(EngagementSession).where(EngagementSession.name == ext_session_id)
        )
        if existing is not None:
            return str(existing.id)
    view = sessions_service.create_session(
        tenant_id,
        name=ext_session_id,
        scope_hosts=[],  # an upload needs no egress scope (S3)
        authorized_by="chrome-extension-capture",
    )
    return view.id


@router.post("/save-files")
def save_files(payload: SaveFilesIn) -> dict:
    settings = get_settings()
    redis = get_redis()
    meta = payload.metadata or {}
    should_analyze = bool(meta.get("performAnalysis")) and not meta.get("disableAnalysis")

    ext_session_id = meta.get("sessionId") or (
        payload.files[0].sessionId if payload.files else None
    )
    tenant_id = _get_or_create_tenant(settings.capture_tenant_name)
    platform_session_id = (
        _get_or_create_session(tenant_id, ext_session_id) if ext_session_id else None
    )
    if platform_session_id is None:
        return {
            "success": True, "stored": 0, "files": [], "sessionId": None,
            "analysis": {"requested": should_analyze, "completed": 0, "failed": 0},
        }

    file_results: list[dict] = []
    completed = failed = 0
    for f in payload.files:
        try:
            view = runs_service.create_run(
                redis, tenant_id=tenant_id, session_id=platform_session_id, target=f.url
            )
            key = storage.put_blob(tenant_id, view.id, "input", f.content.encode("utf-8"))
            with tenant_session(tenant_id) as session:
                session.execute(update(Run).where(Run.id == view.id).values(input_ref=key))
            analysis: dict[str, Any] = {"requested": should_analyze, "status": "skipped"}
            if should_analyze:
                cov = analyze.analyze_run(
                    redis, tenant_id=tenant_id, run_id=view.id, job_id=None
                )
                analysis = {
                    "requested": True, "status": "completed",
                    "attributed": cov.attributed, "unattributed": cov.unattributed,
                    "secrets": cov.secrets, "secrets_engine": cov.secrets_engine,
                    "findings_written": cov.findings_written,
                }
                completed += 1
            file_results.append({
                "fileId": view.id, "runId": view.id, "url": f.url,
                "contentHash": f.contentHash, "analysis": analysis,
            })
        except Exception as exc:  # spike: surface, don't 4xx-drop the batch
            failed += 1
            log.warning("capture.save_files.file_failed", url=f.url, error=str(exc))
            file_results.append({
                "url": f.url, "contentHash": f.contentHash,
                "analysis": {"requested": should_analyze, "status": "failed", "error": str(exc)},
            })

    log.info("capture.save_files", stored=len(file_results), analyzed=completed, failed=failed)
    return {
        "success": True,
        "sessionId": platform_session_id,
        "stored": len(file_results),
        "fileIds": [r["fileId"] for r in file_results if "fileId" in r],
        "files": file_results,
        "analysis": {"requested": should_analyze, "completed": completed, "failed": failed},
        "_spike": {
            "tenant_id": tenant_id, "storage": "local-disk",
            "run_state": "queued (worker skipped; analyze ran inline)",
            "mapping": "one platform run per file",
        },
    }
