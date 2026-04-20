from __future__ import annotations

import asyncio
from datetime import datetime
import shutil
import threading
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from ...db import get_db
from ...models import Session as DbSession
from ...models import Job as DbJob
from ...services.binary_locator import resolve_binary_path
from ...services.recon_job_runner import ReconJobRunner, ReconRunnerOptions
from ...services.security_utils import SecurityValidator


router = APIRouter()

# Stop events are in-process signals — not persisted, not shared across workers.
RECON_JOB_STOP_EVENTS: dict[str, threading.Event] = {}
RECON_LOCK = threading.Lock()  # guards RECON_JOB_STOP_EVENTS only

MISS_REASON_TAXONOMY = (
    "not_seen",
    "fetch_4xx",
    "fetch_5xx",
    "fetch_timeout",
    "non_js_content",
    "blocked_by_scope",
    "parse_failed",
    "dedup_skipped",
)


class ReconJobStartRequest(BaseModel):
    url: str | None = None
    urls: list[str] = Field(default_factory=list)
    sessionId: str | None = None
    sessionName: str | None = Field(default=None, min_length=1, max_length=120)
    sameOriginOnly: bool = True
    maxAssets: int = Field(default=300, ge=1, le=5000)
    maxDepth: int = Field(default=2, ge=0, le=5)
    discoveryEngine: str = Field(default="headless")
    includeSourceMaps: bool = True
    performAnalysis: bool = True
    waitAfterLoadMs: int = Field(default=2500, ge=0, le=30000)
    timeoutSeconds: int = Field(default=20, ge=3, le=120)
    maxResponseBytes: int = Field(default=12 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)


def now_iso() -> str:
    return datetime.utcnow().isoformat()


def _coerce_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except Exception:
        return 0


def _pct(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100, 2)


def build_coverage_snapshot(raw_coverage: dict[str, Any] | None) -> dict[str, Any]:
    coverage = raw_coverage or {}
    discovered_js = _coerce_int(coverage.get("discovered_js"))
    fetched_js = _coerce_int(coverage.get("fetched_js"))
    ingested_js = _coerce_int(coverage.get("ingested_js"))
    analyzed_js = _coerce_int(coverage.get("analyzed_js"))
    map_detected = _coerce_int(coverage.get("map_detected"))
    map_fetched = _coerce_int(coverage.get("map_fetched"))
    map_failed = _coerce_int(coverage.get("map_failed"))
    if map_failed == 0 and map_detected > map_fetched:
        map_failed = map_detected - map_fetched

    reasons_in = coverage.get("failure_reasons") or {}
    reason_counts = {reason: 0 for reason in MISS_REASON_TAXONOMY}
    if isinstance(reasons_in, dict):
        for key, value in reasons_in.items():
            name = str(key or "").strip()
            if name not in reason_counts:
                continue
            reason_counts[name] = _coerce_int(value)

    return {
        "discovered_js": discovered_js,
        "fetched_js": fetched_js,
        "ingested_js": ingested_js,
        "analyzed_js": analyzed_js,
        "map_detected": map_detected,
        "map_fetched": map_fetched,
        "map_processed": map_fetched,
        "map_failed": map_failed,
        "failure_reasons": reason_counts,
        "rates": {
            "fetchPct": _pct(fetched_js, discovered_js),
            "ingestPct": _pct(ingested_js, discovered_js),
            "analysisPct": _pct(analyzed_js, discovered_js),
            "mapFetchPct": _pct(map_fetched, map_detected),
        },
    }


def get_latest_session_capture_coverage(session_id: str, db_session=None) -> dict[str, Any] | None:
    """Return coverage summary for the most-recent recon job in a session.

    db_session is optional so call sites that cannot provide a DB session gracefully
    receive None rather than raising.
    """
    if db_session is None:
        return None
    rows = (
        db_session.query(DbJob)
        .filter(DbJob.job_type == "recon", DbJob.session_id == str(session_id))
        .order_by(DbJob.created_at.desc())
        .all()
    )
    if not rows:
        return None
    latest = rows[0]
    state = dict(latest.state_json or {})
    return {
        "jobId": str(latest.id),
        "jobStatus": latest.status,
        "updatedAt": (
            latest.finished_at.isoformat()
            if latest.finished_at
            else (
                latest.started_at.isoformat()
                if latest.started_at
                else latest.created_at.isoformat()
            )
        ),
        **build_coverage_snapshot(state.get("coverage") or {}),
    }


def build_job_state(job_id: str, request: ReconJobStartRequest, targets: list[str], session_id: str) -> dict[str, Any]:
    return {
        "jobId": job_id,
        "status": "queued",
        "createdAt": now_iso(),
        "startedAt": None,
        "finishedAt": None,
        "cancelRequested": False,
        "cancelRequestedAt": None,
        "sessionId": session_id,
        "targets": targets,
        "options": {
            "sameOriginOnly": request.sameOriginOnly,
            "maxAssets": request.maxAssets,
            "maxDepth": request.maxDepth,
            "discoveryEngine": request.discoveryEngine,
            "includeSourceMaps": request.includeSourceMaps,
            "performAnalysis": request.performAnalysis,
            "waitAfterLoadMs": request.waitAfterLoadMs,
            "timeoutSeconds": request.timeoutSeconds,
            "maxResponseBytes": request.maxResponseBytes,
        },
        "assets": {},
        "coverage": build_coverage_snapshot(None),
        "summary": {
            "stored": 0,
            "fileIds": [],
            "cancelled": False,
        },
        "error": None,
    }


def get_public_job_snapshot(job_id: str, db_session) -> dict[str, Any] | None:
    """Read job from DB and return the public snapshot dict."""
    try:
        job_uuid = uuid.UUID(job_id) if isinstance(job_id, str) else job_id
    except (ValueError, AttributeError):
        return None
    row = db_session.query(DbJob).filter(DbJob.id == job_uuid).first()
    if not row:
        return None
    payload = dict(row.state_json or {})
    assets = sorted(payload.get("assets", {}).values(), key=lambda r: r.get("discoveredAt") or "")
    payload["assets"] = assets
    payload["assetCount"] = len(assets)
    payload["coverage"] = build_coverage_snapshot(payload.get("coverage") or {})
    return payload


def update_job_asset(job_id: str, asset: dict[str, Any], db_session) -> None:
    with RECON_LOCK:
        job_uuid = uuid.UUID(job_id) if isinstance(job_id, str) else job_id
        row = db_session.query(DbJob).filter(DbJob.id == job_uuid).first()
        if not row:
            return
        url = asset.get("url")
        if not url:
            return
        state = dict(row.state_json or {})
        state.setdefault("assets", {})[url] = dict(asset)
        state["coverage"] = recompute_job_coverage_from_assets(state.get("assets") or {})
        row.state_json = state
        db_session.commit()


def recompute_job_coverage_from_assets(assets_by_url: dict[str, Any]) -> dict[str, Any]:
    assets = list((assets_by_url or {}).values())
    failure_reasons = {reason: 0 for reason in MISS_REASON_TAXONOMY}
    discovered_js = len(assets)
    fetched_js = 0
    ingested_js = 0
    analyzed_js = 0
    map_detected = 0
    map_fetched = 0
    dedup_skipped = 0

    for asset in assets:
        if asset.get("fetched"):
            fetched_js += 1
        if asset.get("ingested"):
            ingested_js += 1
        if asset.get("analyzed"):
            analyzed_js += 1
        if asset.get("sourceMapDetectedUrl"):
            map_detected += 1
        if asset.get("sourceMapFetched"):
            map_fetched += 1
        dedup_skipped += max(0, int(asset.get("duplicateCount") or 0))

        reason = str(asset.get("failureReason") or "").strip().lower()
        if reason in failure_reasons:
            failure_reasons[reason] += 1
        elif reason.startswith("fetch_4"):
            failure_reasons["fetch_4xx"] += 1
        elif reason.startswith("fetch_5"):
            failure_reasons["fetch_5xx"] += 1
        elif reason.startswith("timeout"):
            failure_reasons["fetch_timeout"] += 1
        elif reason.startswith("blocked"):
            failure_reasons["blocked_by_scope"] += 1
        elif reason.startswith("parse"):
            failure_reasons["parse_failed"] += 1
        elif not asset.get("fetched"):
            failure_reasons["not_seen"] += 1

    failure_reasons["dedup_skipped"] = dedup_skipped
    map_failed = max(0, map_detected - map_fetched)
    return {
        "discovered_js": discovered_js,
        "fetched_js": fetched_js,
        "ingested_js": ingested_js,
        "analyzed_js": analyzed_js,
        "map_detected": map_detected,
        "map_fetched": map_fetched,
        "map_processed": map_fetched,
        "map_failed": map_failed,
        "failure_reasons": failure_reasons,
        "rates": {
            "fetchPct": _pct(fetched_js, discovered_js),
            "ingestPct": _pct(ingested_js, discovered_js),
            "analysisPct": _pct(analyzed_js, discovered_js),
            "mapFetchPct": _pct(map_fetched, map_detected),
        },
    }


def finalize_job(job_id: str, status: str, result: dict[str, Any] | None, error: str | None = None, db_session=None) -> None:
    if db_session is None:
        return
    job_uuid = uuid.UUID(job_id) if isinstance(job_id, str) else job_id
    row = db_session.query(DbJob).filter(DbJob.id == job_uuid).first()
    if not row:
        return
    state = dict(row.state_json or {})
    state["status"] = status
    state["finishedAt"] = now_iso()
    state["error"] = error
    if result:
        state["coverage"] = build_coverage_snapshot(result.get("coverage", state.get("coverage", {})))
        ingestion = result.get("ingestion") or {}
        state["summary"] = {
            "stored": int(ingestion.get("stored") or 0),
            "fileIds": ingestion.get("fileIds") or [],
            "cancelled": bool(result.get("cancelled")),
        }
    row.status = status
    row.finished_at = datetime.utcnow()
    row.error = error
    row.state_json = state
    db_session.commit()


def run_recon_job_worker(
    job_id: str,
    options: ReconRunnerOptions,
    worker_session_factory: sessionmaker,
) -> None:
    db = worker_session_factory()
    job_uuid = uuid.UUID(job_id) if isinstance(job_id, str) else job_id
    try:
        # Mark running
        row = db.query(DbJob).filter(DbJob.id == job_uuid).first()
        if not row:
            return
        state = dict(row.state_json or {})
        state["status"] = "running"
        state["startedAt"] = now_iso()
        row.status = "running"
        row.started_at = datetime.utcnow()
        row.state_json = state
        db.commit()

        stop_event = RECON_JOB_STOP_EVENTS.get(job_id) or threading.Event()
        with RECON_LOCK:
            RECON_JOB_STOP_EVENTS[job_id] = stop_event

        runner = ReconJobRunner(
            options=options,
            db=db,
            progress_callback=lambda asset: update_job_asset(job_id, asset, db),
            should_stop=lambda: stop_event.is_set(),
        )
        result = asyncio.run(runner.run())
        final_status = "cancelled" if stop_event.is_set() else "completed"
        finalize_job(job_id, final_status, result=result, error=None, db_session=db)
    except Exception as exc:
        try:
            finalize_job(job_id, "failed", result=None, error=str(exc), db_session=db)
        except Exception:
            pass
    finally:
        db.close()


@router.post("/api/recon/jobs/start")
def start_recon_job(request: ReconJobStartRequest, db: Session = Depends(get_db)):
    targets: list[str] = []
    if request.url:
        targets.append(request.url)
    targets.extend(request.urls or [])
    targets = list(dict.fromkeys(targets))
    if not targets:
        raise HTTPException(status_code=400, detail="At least one target URL is required")

    validated_targets: list[str] = []
    for target in targets:
        try:
            validated_targets.append(SecurityValidator.validate_url(target))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid target URL '{target}': {exc}")

    discovery_engine = str(request.discoveryEngine or "headless").strip().lower()
    if discovery_engine not in {"headless", "katana", "hybrid", "vespasian"}:
        raise HTTPException(status_code=422, detail="Invalid discoveryEngine. Use headless, katana, hybrid, or vespasian")
    katana_binary = resolve_binary_path("katana", env_var="KATANA_BINARY")
    if discovery_engine == "katana" and not katana_binary:
        raise HTTPException(
            status_code=422,
            detail="Katana engine requested but katana binary is not available in the current API runtime. Install katana or use headless/hybrid.",
        )
    vespasian_binary = resolve_binary_path("vespasian", env_var="VESPASIAN_BINARY")
    if discovery_engine == "vespasian" and not vespasian_binary:
        raise HTTPException(
            status_code=422,
            detail=(
                "Vespasian engine requested but the vespasian binary is not available. "
                "Install from https://github.com/praetorian-inc/vespasian or set "
                "the VESPASIAN_BINARY environment variable."
            ),
        )

    raw_session_id = request.sessionId or str(uuid.uuid4())
    try:
        session_uuid = uuid.UUID(str(raw_session_id))
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid sessionId: must be a UUID")
    session_id = str(session_uuid)
    session_name = (request.sessionName or "").strip() or None

    db_session_obj = db.query(DbSession).filter(DbSession.id == session_uuid).first()
    created_session = False
    if not db_session_obj:
        created_session = True
        db_session_obj = DbSession(
            id=session_uuid,
            name=session_name,
            source=f"recon_{discovery_engine}",
            version="3.0.0",
        )
        db.add(db_session_obj)
    elif session_name:
        db_session_obj.name = session_name
    db.commit()

    job_id = str(uuid.uuid4())
    options = ReconRunnerOptions(
        urls=validated_targets,
        session_id=session_id,
        same_origin_only=request.sameOriginOnly,
        max_assets=request.maxAssets,
        max_depth=request.maxDepth,
        discovery_engine=discovery_engine,
        katana_binary=katana_binary or "katana",
        vespasian_binary=vespasian_binary or "vespasian",
        include_sourcemaps=request.includeSourceMaps,
        perform_analysis=request.performAnalysis,
        wait_after_load_ms=request.waitAfterLoadMs,
        timeout_seconds=request.timeoutSeconds,
        max_response_bytes=request.maxResponseBytes,
    )

    initial_state = build_job_state(job_id, request, validated_targets, session_id)
    db_job = DbJob(
        id=uuid.UUID(job_id),
        job_type="recon",
        session_id=session_id,
        status="queued",
        state_json=initial_state,
    )
    db.add(db_job)
    with RECON_LOCK:
        RECON_JOB_STOP_EVENTS[job_id] = threading.Event()
    db.commit()

    worker_session_factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db.bind,
    )
    thread = threading.Thread(
        target=run_recon_job_worker,
        args=(job_id, options, worker_session_factory),
        daemon=True,
    )
    thread.start()

    snapshot = get_public_job_snapshot(job_id, db)
    return {
        "success": True,
        "started": True,
        "jobId": job_id,
        "sessionId": session_id,
        "sessionCreated": created_session,
        "job": snapshot,
    }


@router.get("/api/recon/jobs")
def list_recon_jobs(db: Session = Depends(get_db)):
    rows = db.query(DbJob).filter(DbJob.job_type == "recon").order_by(DbJob.created_at.desc()).all()
    jobs = [get_public_job_snapshot(str(row.id), db) for row in rows]
    jobs = [j for j in jobs if j]
    return {
        "success": True,
        "count": len(jobs),
        "jobs": jobs,
    }


@router.get("/api/recon/jobs/{job_id}")
def get_recon_job(job_id: str, db: Session = Depends(get_db)):
    snapshot = get_public_job_snapshot(job_id, db)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Recon job not found")
    return {
        "success": True,
        "job": snapshot,
    }


@router.post("/api/recon/jobs/{job_id}/stop")
def stop_recon_job(job_id: str, db: Session = Depends(get_db)):
    try:
        job_uuid = uuid.UUID(job_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="Recon job not found")
    row = db.query(DbJob).filter(DbJob.id == job_uuid).first()
    if not row:
        raise HTTPException(status_code=404, detail="Recon job not found")
    status = str(row.status or "").lower()
    if status in {"completed", "failed", "cancelled"}:
        return {
            "success": True,
            "stopRequested": False,
            "message": f"Job already finished with status '{status}'",
            "job": get_public_job_snapshot(job_id, db),
        }
    state = dict(row.state_json or {})
    state["cancelRequested"] = True
    state["cancelRequestedAt"] = now_iso()
    if status == "queued":
        state["status"] = "cancelling"
        row.status = "cancelling"
    row.cancel_requested = True
    row.cancel_requested_at = datetime.utcnow()
    row.state_json = state
    db.commit()
    # Signal in-process stop event
    with RECON_LOCK:
        ev = RECON_JOB_STOP_EVENTS.get(job_id)
        if ev:
            ev.set()
    return {
        "success": True,
        "stopRequested": True,
        "message": "Stop requested. Job will halt after current operation.",
        "job": get_public_job_snapshot(job_id, db),
    }
