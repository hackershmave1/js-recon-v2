"""Extension -> platform ingest (Phase 1, flag-gated).

Accepts the Chrome extension's batched ``POST /api/save-files`` and accumulates a
capture SESSION's files into ONE platform ``Run`` (run-per-capture-session): each
batch stores its blobs to S3 and seeds pre-fetched ``run_asset`` rows; analysis is
worker-driven, triggered once by ``POST /api/sessions/{id}/analyze/start`` which
emits the ``discover.assets`` event and enqueues the DISCOVERING stage. The worker
then walks DISCOVERING (no-op: the event short-circuits the crawl) -> FETCHING
(no-op: every asset is already ``fetch_ok`` with its uploaded blob, so nothing is
egressed) -> INGESTING/CORRELATING (no-op stubs) -> ANALYZING (real) -> finalize.
Mounted only when ``settings.enable_capture_ingest`` is true (see ``api/app.py``).

Idempotency (trap T6, settled "run-per-capture-session"): a retried batch re-stores
the SAME content-addressed blob key, ``seed_pending`` skips the existing
``(run_id, url)`` row, and an already-``fetch_ok`` asset is left untouched — so a
retry never makes a duplicate run or asset. No client idempotency key, no schema
change.

Shaped by the §4 adversarial design review — two deliberate omissions on the
session-create hot path:
- NO ``engagement_id`` / ``scope_hosts`` from client metadata. An invalid
  ``projectId`` or scope host raises in ``create_session``; on this path that
  surfaces as a permanent 4xx (the extension DROPS un-recapturable JS) or a 5xx
  retry loop. Project binding + scope seeding are Phase 2 (with ``/api/projects``).
  Scope is inert here anyway: captured assets are pre-fetched, never egressed.
- Files are validated PER FILE inside the handler, not by a body-level pydantic
  model, so one malformed file can't 422 (and lose) the whole batch.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from redis import Redis
from sqlalchemy import exists, select

from recon import storage
from recon.api.deps import get_redis
from recon.config import get_settings
from recon.db.base import admin_session, tenant_session
from recon.db.models import EngagementSession, Job, Run, RunAsset, Tenant
from recon.domain import TERMINAL_STATES, AssetStatus, RunStage, RunState
from recon.engagements import service as engagements_service
from recon.events.log import publish, record_event
from recon.observability import get_logger
from recon.runs import assets, coordinator
from recon.runs import service as runs_service
from recon.sessions import service as sessions_service

log = get_logger("recon.api.capture")

router = APIRouter(prefix="/api", tags=["capture"])


class SaveFilesIn(BaseModel):
    """Lenient by design: ``files`` is a list of raw dicts validated per-file in
    the handler (see the module docstring), never a body-level pydantic model."""

    metadata: dict[str, Any] = Field(default_factory=dict)
    files: list[dict[str, Any]] = Field(default_factory=list)


@router.get("/health")
def capture_health() -> dict:
    """Mirror the extension's workspace-client health probe."""
    return {"status": "ok", "mode": "platform"}


# --------------------------------------------------------------------------- #
# Tenant + session resolution (single capture tenant; no X-Tenant-Id header).
# --------------------------------------------------------------------------- #


def _get_or_create_tenant(name: str) -> str:
    with admin_session() as session:
        existing = session.scalar(select(Tenant).where(Tenant.name == name))
        if existing is not None:
            return str(existing.id)
    # No row yet — create_tenant opens its own admin_session.
    return sessions_service.create_tenant(name)


def _find_session_by_name(tenant_id: str, ext_session_id: str) -> str | None:
    with tenant_session(tenant_id) as session:
        row = session.scalar(
            select(EngagementSession).where(EngagementSession.name == ext_session_id)
        )
        return str(row.id) if row is not None else None


def _safe_uuid(value: Any) -> str | None:
    """Canonical UUID string, or None for a falsy/malformed value — so a bad
    ``projectId`` can never reach a DB lookup that would raise (StatementError)."""
    if not value:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def _get_or_create_session(
    tenant_id: str, ext_session_id: str, engagement_id: str | None = None
) -> str:
    # Map the extension's sessionId -> a platform session idempotently, keyed by
    # name, so a retried batch reuses the session instead of piling up duplicates.
    existing = _find_session_by_name(tenant_id, ext_session_id)
    if existing is not None:
        return existing
    # EMPTY scope (§4 defect B: scope is inert here — captured assets never egress).
    # Bind the engagement (projectId) if it resolves cleanly, but NEVER raise on the
    # ingest hot path (§4 defect A): a foreign/deleted engagement makes create_session
    # raise SessionInvalid *before* any row is added, so we retry unbound. A malformed
    # id was already filtered to None by _safe_uuid upstream.
    try:
        view = sessions_service.create_session(
            tenant_id, name=ext_session_id, scope_hosts=[],
            authorized_by="chrome-extension-capture", engagement_id=engagement_id,
        )
    except sessions_service.SessionInvalid:
        log.warning(
            "capture.session.engagement_ignored", session=ext_session_id, engagement_id=engagement_id
        )
        view = sessions_service.create_session(
            tenant_id, name=ext_session_id, scope_hosts=[],
            authorized_by="chrome-extension-capture", engagement_id=None,
        )
    return view.id


# --------------------------------------------------------------------------- #
# Accumulating run: one open Run per capture session, appended to across batches.
# --------------------------------------------------------------------------- #


def _accumulating_run_id(tenant_id: str, session_id: str, redis: Redis) -> str:
    """The session's open run to append this batch to: its latest QUEUED run that
    has no Job yet. Once ``analyze/start`` enqueues a Job the run is "sealed", so
    the next batch opens a fresh run (a new capture round). ``target`` stays None:
    an upload run must never be crawled/fetched — the discover/fetch stages no-op
    on a target-less, pre-fetched run (``crawl.py:45`` / ``fetch.py:171``)."""
    with tenant_session(tenant_id) as session:
        run_id = session.scalar(
            select(Run.id)
            .where(
                Run.session_id == str(session_id),
                Run.state == RunState.QUEUED.value,
                ~exists(select(Job.id).where(Job.run_id == Run.id)),
            )
            .order_by(Run.created_at.desc())
            .limit(1)
        )
        if run_id is not None:
            return str(run_id)
    view = runs_service.create_run(
        redis, tenant_id=tenant_id, session_id=str(session_id), target=None
    )
    return view.id


def _valid_file(f: dict, max_bytes: int) -> tuple[str, bytes] | None:
    """``(url, content_bytes)`` for a well-formed, within-cap file, else ``None``
    (a per-file failure — never a batch-wide 422). The cap bounds worker memory
    (REQ-Q5): the analyze stage reads the whole blob in."""
    url = f.get("url")
    content = f.get("content")
    if not isinstance(url, str) or not url or not isinstance(content, str):
        return None
    data = content.encode("utf-8")
    if len(data) > max_bytes:
        return None
    return url, data


def _seed_fetched_assets(tenant_id: str, run_id: str, keys_by_url: dict[str, str]) -> None:
    """Seed this batch's urls as ``run_asset`` rows and mark each ``fetch_ok`` with
    its uploaded blob key — in ONE transaction, so a row is never left committed as
    PENDING-without-``input_ref`` (which the FETCHING stage would try to egress).
    Idempotent: ``seed_pending`` skips an existing ``(run_id, url)``; a url already
    ``fetch_ok`` is left as-is (first-wins — a retry or a later same-url batch never
    clobbers the original blob)."""
    with tenant_session(tenant_id) as session:
        assets.seed_pending(session, tenant_id=tenant_id, run_id=run_id, urls=list(keys_by_url))
        session.flush()  # make the seeded rows visible to the query below, same tx
        by_url = {
            row.url: row
            for row in session.scalars(select(RunAsset).where(RunAsset.run_id == str(run_id)))
        }
        for url, key in keys_by_url.items():
            row = by_url.get(url)
            if row is not None and row.fetch_status == AssetStatus.PENDING.value and not row.input_ref:
                assets.set_fetch_ok(session, str(row.id), key)


@router.post("/save-files")
def save_files(payload: SaveFilesIn) -> dict:
    settings = get_settings()
    redis = get_redis()
    meta = payload.metadata or {}
    ext_session_id = meta.get("sessionId") or (
        payload.files[0].get("sessionId") if payload.files else None
    )
    tenant_id = _get_or_create_tenant(settings.capture_tenant_name)
    if not ext_session_id:
        return {
            "success": True, "sessionId": None, "runId": None,
            "stored": 0, "failed": 0, "files": [],
        }
    engagement_id = _safe_uuid(meta.get("projectId"))  # bind the project if it resolves; else unbound
    session_id = _get_or_create_session(tenant_id, ext_session_id, engagement_id)
    run_id = _accumulating_run_id(tenant_id, session_id, redis)

    file_results: list[dict] = []
    keys_by_url: dict[str, str] = {}
    stored = failed = 0
    for f in payload.files:
        content_hash = f.get("contentHash")
        parsed = _valid_file(f, settings.max_upload_bytes)
        if parsed is None:
            failed += 1
            file_results.append({
                "url": f.get("url"), "contentHash": content_hash,
                "stored": False, "error": "invalid or oversized file",
            })
            continue
        url, data = parsed
        if url in keys_by_url:
            # A repeat url within one batch: keep the first (first-wins). Don't
            # store an orphan blob or double-count — the asset already maps to the
            # first file's content.
            file_results.append({
                "url": url, "contentHash": content_hash,
                "stored": False, "error": "duplicate url in batch",
            })
            continue
        try:
            key = storage.put_blob(tenant_id, run_id, "input", data)
        except Exception as exc:  # infra: 5xx so the extension RETRIES the whole (idempotent) batch
            log.error("capture.save_files.blob_failed", url=url, error=str(exc))
            raise HTTPException(status_code=503, detail="blob storage unavailable") from exc
        keys_by_url[url] = key
        stored += 1
        file_results.append({
            "url": url, "contentHash": content_hash, "runId": run_id, "stored": True,
        })

    if keys_by_url:
        _seed_fetched_assets(tenant_id, run_id, keys_by_url)

    log.info("capture.save_files", session=ext_session_id, run_id=run_id, stored=stored, failed=failed)
    return {
        "success": True,
        "sessionId": session_id,
        "runId": run_id,
        "stored": stored,
        "failed": failed,
        "files": file_results,
    }


# --------------------------------------------------------------------------- #
# analyze/start: emit discover.assets + enqueue the worker once, on the session's
# latest run (the one /save-files accumulated into).
# --------------------------------------------------------------------------- #


def _latest_run_id(tenant_id: str, session_id: str) -> str | None:
    with tenant_session(tenant_id) as session:
        run_id = session.scalar(
            select(Run.id)
            .where(Run.session_id == str(session_id))
            .order_by(Run.created_at.desc())
            .limit(1)
        )
        return str(run_id) if run_id is not None else None


def _run_has_job(tenant_id: str, run_id: str) -> bool:
    with tenant_session(tenant_id) as session:
        return bool(
            session.scalar(select(exists(select(Job.id).where(Job.run_id == str(run_id)))))
        )


def _manifest_domain(rows: list, fallback: str) -> str:
    for row in rows:
        host = urlsplit(row.url).hostname
        if host:
            return host
    return fallback


@router.post("/sessions/{ext_session_id}/analyze/start")
def analyze_start(ext_session_id: str) -> dict:
    redis = get_redis()
    settings = get_settings()
    tenant_id = _get_or_create_tenant(settings.capture_tenant_name)
    session_id = _find_session_by_name(tenant_id, ext_session_id)
    if session_id is None:
        raise HTTPException(status_code=404, detail="unknown capture session")

    run_id = _latest_run_id(tenant_id, session_id)
    if run_id is None:
        return {"started": False, "message": "no run to analyze"}
    rows = assets.list_for_run(tenant_id, run_id)
    if not rows:
        return {"started": False, "message": "no captured files to analyze"}
    if _run_has_job(tenant_id, run_id):
        # Already enqueued (idempotent: a retried analyze/start, or the run is a
        # completed prior round). Do not enqueue a second walk.
        return {"started": True, "message": "analysis already started", "runId": run_id}

    # Store an assets manifest so GET /runs/{id}/assets reads back (trap T5), then
    # emit the discover.assets event: the coordinator uses it to (a) short-circuit
    # the crawl stage and (b) finalize DONE/PARTIAL from per-asset status. status
    # MUST be the literal "ok" (coordinator._finalize_state).
    manifest = {
        "domain": _manifest_domain(rows, ext_session_id),
        "status": "ok",
        "assets": [{"url": r.url, "source": "extension"} for r in rows],
    }
    assets_ref = storage.put_blob(
        tenant_id, run_id, "assets", json.dumps(manifest).encode("utf-8")
    )
    with tenant_session(tenant_id) as session:
        event = record_event(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            event_type="discover.assets",
            payload={"count": len(rows), "assets_ref": assets_ref, "status": "ok"},
        )
    publish(redis, event)  # after commit — a subscriber must never see an unpersisted event
    job_id = coordinator.enqueue_stage(
        redis, tenant_id=tenant_id, run_id=run_id, stage=RunStage.DISCOVERING
    )
    log.info("capture.analyze_start", session=ext_session_id, run_id=run_id, count=len(rows), job=job_id)
    return {"started": True, "job": job_id, "runId": run_id}


# --------------------------------------------------------------------------- #
# analyze/progress: adapt the run's per-asset status into the extension popup's
# `job` shape (counts + per-file url/status). See workspace-client.getAnalysisProgress.
# --------------------------------------------------------------------------- #


def _latest_analyzed_run(tenant_id: str, session_id: str) -> tuple[str, str] | None:
    """The session's latest run that has been ENQUEUED for analysis (has a Job),
    with its state. A never-analyzed accumulating run (QUEUED, no Job) is excluded
    on purpose: the popup would read pending assets as "running" and disable the
    Analyze button (§4). Excluding it makes progress report idle there instead, and
    preferring the latest enqueued run keeps a finished round visible after the next
    capture round opens a fresh accumulating run."""
    with tenant_session(tenant_id) as session:
        row = session.execute(
            select(Run.id, Run.state)
            .where(
                Run.session_id == str(session_id),
                exists(select(Job.id).where(Job.run_id == Run.id)),
            )
            .order_by(Run.created_at.desc())
            .limit(1)
        ).first()
        return (str(row[0]), row[1]) if row is not None else None


def _asset_progress_status(row: assets.AssetRow, *, run_analyzing: bool, run_terminal: bool) -> str:
    """Map a capture asset to the popup's per-file vocabulary. Captured assets are
    pre-fetch_ok, so the signal is analyze_status; a fetch failure still surfaces.
    A still-``pending`` asset on a TERMINAL run (abnormal termination: analyze
    retries exhausted -> FAILED, or CANCELLED) settles to ``failed`` so the popup's
    ``inFlight = queued + analyzing`` reaches 0 instead of polling "running" forever."""
    if row.analyze_status == AssetStatus.OK.value:
        return "completed"
    if row.analyze_status == AssetStatus.FAILED.value or row.fetch_status == AssetStatus.FAILED.value:
        return "failed"
    if run_terminal:
        return "failed"
    return "analyzing" if run_analyzing else "queued"


def _idle_job() -> dict:
    return {
        "counts": {"queued": 0, "analyzing": 0, "completed": 0, "failed": 0, "cancelled": 0, "total": 0},
        "files": [],
    }


@router.get("/sessions/{ext_session_id}/analyze/progress")
def analyze_progress(ext_session_id: str) -> dict:
    settings = get_settings()
    tenant_id = _get_or_create_tenant(settings.capture_tenant_name)
    session_id = _find_session_by_name(tenant_id, ext_session_id)
    if session_id is None:
        raise HTTPException(status_code=404, detail="unknown capture session")

    latest = _latest_analyzed_run(tenant_id, session_id)
    if latest is None:
        return {"success": True, "sessionId": session_id, "job": _idle_job()}
    run_id, state = latest
    run_state = RunState(state)
    run_analyzing = run_state == RunState.ANALYZING
    run_terminal = run_state in TERMINAL_STATES
    rows = assets.list_for_run(tenant_id, run_id)
    counts = {"queued": 0, "analyzing": 0, "completed": 0, "failed": 0, "cancelled": 0, "total": len(rows)}
    files = []
    for row in rows:
        status = _asset_progress_status(row, run_analyzing=run_analyzing, run_terminal=run_terminal)
        counts[status] += 1
        files.append({"url": row.url, "status": status})
    return {"success": True, "sessionId": session_id, "job": {"counts": counts, "files": files}}


# --------------------------------------------------------------------------- #
# projects <-> engagements: the extension's project = a v2 engagement. GET must be
# a BARE ARRAY (the extension does Array.isArray(body)?body:[]); the project id is
# `id` (not engagement_id); a `defaults` config doc is synthesized from the
# engagement's scope + v1 system defaults (the extension only reads scope + creates
# name+rootDomains, so nothing it uses is lost).
# --------------------------------------------------------------------------- #


def _engagement_to_project(view: engagements_service.EngagementView) -> dict:
    return {
        "id": view.id,
        "name": view.name,
        "createdAt": view.created_at,
        "updatedAt": view.updated_at,
        "defaults": {
            "scope": {"rootDomains": list(view.in_scope_domains), "includeSubdomains": True},
            "capture": {"outOfScopeMode": "tag", "maxAssetMb": 10},
            "denylist": {"rules": [], "useDefaultProfile": True},
            "analysis": {"analyzeOnUpload": False, "captureSourceMaps": True},
        },
    }


@router.get("/projects")
def list_projects() -> list[dict]:
    settings = get_settings()
    tenant_id = _get_or_create_tenant(settings.capture_tenant_name)
    return [_engagement_to_project(v) for v in engagements_service.list_engagements(tenant_id)]


@router.post("/projects")
def create_project(payload: dict[str, Any]) -> dict:
    settings = get_settings()
    tenant_id = _get_or_create_tenant(settings.capture_tenant_name)
    name = str(payload.get("name") or "").strip()
    if not name:
        # create is user-initiated (not the JS-loss ingest path); a string detail
        # matches the platform's engagements 400 and renders cleanly in the popup.
        raise HTTPException(status_code=400, detail="a project name is required")
    defaults = payload.get("defaults") if isinstance(payload.get("defaults"), dict) else {}
    scope = defaults.get("scope") if isinstance(defaults.get("scope"), dict) else {}
    raw_domains = scope.get("rootDomains")
    root_domains = [str(d) for d in raw_domains] if isinstance(raw_domains, list) else []
    view = engagements_service.create_engagement(
        tenant_id, name=name, in_scope_domains=root_domains, out_of_scope_domains=[]
    )
    return _engagement_to_project(view)
