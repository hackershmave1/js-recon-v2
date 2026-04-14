from __future__ import annotations

import asyncio
import copy
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
from ...services.recon_job_runner import ReconJobRunner, ReconRunnerOptions
from ...services.security_utils import SecurityValidator


router = APIRouter()

RECON_JOBS: dict[str, dict[str, Any]] = {}
RECON_JOB_STOP_EVENTS: dict[str, threading.Event] = {}
RECON_LOCK = threading.Lock()
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


def get_latest_session_capture_coverage(session_id: str) -> dict[str, Any] | None:
    with RECON_LOCK:
        matching_jobs = [
            copy.deepcopy(job)
            for job in RECON_JOBS.values()
            if str(job.get("sessionId") or "") == str(session_id)
        ]
    if not matching_jobs:
        return None

    matching_jobs.sort(
        key=lambda row: row.get("finishedAt") or row.get("startedAt") or row.get("createdAt") or "",
        reverse=True,
    )
    latest = matching_jobs[0]
    return {
        "jobId": latest.get("jobId"),
        "jobStatus": latest.get("status"),
        "updatedAt": latest.get("finishedAt") or latest.get("startedAt") or latest.get("createdAt"),
        **build_coverage_snapshot(latest.get("coverage") or {}),
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


def get_public_job_snapshot(job_id: str) -> dict[str, Any] | None:
    with RECON_LOCK:
        state = RECON_JOBS.get(job_id)
        if not state:
            return None
        payload = copy.deepcopy(state)
    assets = sorted(payload.get("assets", {}).values(), key=lambda row: row.get("discoveredAt") or "")
    payload["assets"] = assets
    payload["assetCount"] = len(assets)
    payload["coverage"] = build_coverage_snapshot(payload.get("coverage") or {})
    return payload


def update_job_asset(job_id: str, asset: dict[str, Any]) -> None:
    with RECON_LOCK:
        job = RECON_JOBS.get(job_id)
        if not job:
            return
        url = asset.get("url")
        if not url:
            return
        job["assets"][url] = dict(asset)
        job["coverage"] = recompute_job_coverage_from_assets(job.get("assets") or {})


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


def finalize_job(job_id: str, status: str, result: dict[str, Any] | None, error: str | None = None) -> None:
    with RECON_LOCK:
        job = RECON_JOBS.get(job_id)
        if not job:
            return
        job["status"] = status
        job["finishedAt"] = now_iso()
        job["error"] = error
        if result:
            job["coverage"] = build_coverage_snapshot(result.get("coverage", job.get("coverage", {})))
            ingestion = result.get("ingestion") or {}
            job["summary"] = {
                "stored": int(ingestion.get("stored") or 0),
                "fileIds": ingestion.get("fileIds") or [],
                "cancelled": bool(result.get("cancelled")),
            }


def run_recon_job_worker(
    job_id: str,
    options: ReconRunnerOptions,
    worker_session_factory: sessionmaker,
) -> None:
    with RECON_LOCK:
        job = RECON_JOBS.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["startedAt"] = now_iso()

    db = worker_session_factory()
    stop_event = RECON_JOB_STOP_EVENTS.get(job_id) or threading.Event()
    RECON_JOB_STOP_EVENTS[job_id] = stop_event

    try:
        runner = ReconJobRunner(
            options=options,
            db=db,
            progress_callback=lambda asset: update_job_asset(job_id, asset),
            should_stop=lambda: stop_event.is_set(),
        )
        result = asyncio.run(runner.run())
        final_status = "cancelled" if stop_event.is_set() else "completed"
        finalize_job(job_id, final_status, result=result, error=None)
    except Exception as exc:
        finalize_job(job_id, "failed", result=None, error=str(exc))
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
    if discovery_engine not in {"headless", "katana", "hybrid"}:
        raise HTTPException(status_code=422, detail="Invalid discoveryEngine. Use headless, katana, or hybrid")
    if discovery_engine == "katana" and not shutil.which("katana"):
        raise HTTPException(
            status_code=422,
            detail="Katana engine requested but katana binary is not available in the API container. Install katana or use headless/hybrid.",
        )

    raw_session_id = request.sessionId or str(uuid.uuid4())
    try:
        session_uuid = uuid.UUID(str(raw_session_id))
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid sessionId: must be a UUID")
    session_id = str(session_uuid)
    session_name = (request.sessionName or "").strip() or None

    db_session = db.query(DbSession).filter(DbSession.id == session_uuid).first()
    created_session = False
    if not db_session:
        created_session = True
        db_session = DbSession(
            id=session_uuid,
            name=session_name,
            source=f"recon_{discovery_engine}",
            version="3.0.0",
        )
        db.add(db_session)
    elif session_name:
        db_session.name = session_name
    db.commit()

    job_id = str(uuid.uuid4())
    options = ReconRunnerOptions(
        urls=validated_targets,
        session_id=session_id,
        same_origin_only=request.sameOriginOnly,
        max_assets=request.maxAssets,
        max_depth=request.maxDepth,
        discovery_engine=discovery_engine,
        include_sourcemaps=request.includeSourceMaps,
        perform_analysis=request.performAnalysis,
        wait_after_load_ms=request.waitAfterLoadMs,
        timeout_seconds=request.timeoutSeconds,
        max_response_bytes=request.maxResponseBytes,
    )

    with RECON_LOCK:
        RECON_JOBS[job_id] = build_job_state(job_id, request, validated_targets, session_id)
        RECON_JOB_STOP_EVENTS[job_id] = threading.Event()

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

    snapshot = get_public_job_snapshot(job_id)
    return {
        "success": True,
        "started": True,
        "jobId": job_id,
        "sessionId": session_id,
        "sessionCreated": created_session,
        "job": snapshot,
    }


@router.get("/api/recon/jobs")
def list_recon_jobs():
    with RECON_LOCK:
        ids = list(RECON_JOBS.keys())
    jobs = [get_public_job_snapshot(job_id) for job_id in ids]
    jobs = [job for job in jobs if job]
    jobs.sort(key=lambda row: row.get("createdAt") or "", reverse=True)
    return {
        "success": True,
        "count": len(jobs),
        "jobs": jobs,
    }


@router.get("/api/recon/jobs/{job_id}")
def get_recon_job(job_id: str):
    snapshot = get_public_job_snapshot(job_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Recon job not found")
    return {
        "success": True,
        "job": snapshot,
    }


@router.post("/api/recon/jobs/{job_id}/stop")
def stop_recon_job(job_id: str):
    with RECON_LOCK:
        job = RECON_JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Recon job not found")
        status = str(job.get("status") or "").lower()
        if status in {"completed", "failed", "cancelled"}:
            return {
                "success": True,
                "stopRequested": False,
                "message": f"Job already finished with status '{status}'",
                "job": get_public_job_snapshot(job_id),
            }
        job["cancelRequested"] = True
        job["cancelRequestedAt"] = now_iso()
        if status == "queued":
            job["status"] = "cancelling"

    stop_event = RECON_JOB_STOP_EVENTS.get(job_id)
    if stop_event:
        stop_event.set()

    return {
        "success": True,
        "stopRequested": True,
        "message": "Stop requested. Job will halt after current operation.",
        "job": get_public_job_snapshot(job_id),
    }
