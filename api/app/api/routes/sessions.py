from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import os
import shutil
import threading
import copy

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import case, func
import uuid

from ...config import settings
from ...db import get_db
from ...models import Session as DbSession
from ...models import File as DbFile
from ...models import FileAnalysis as DbFileAnalysis
from ...models import SourceMap as DbSourceMap
from ...models import Dependency as DbDependency
from ...models import Job as DbJob
from ...services.comprehensive_extractor import ComprehensiveExtractor
from ...services.file_analysis_persistence import get_or_create_analyzing_file_analysis
from ...services.secret_rollup import SecretRollupService
from ...services.sourcemap_validation import derive_validation_state, summarize_validation
from .recon import get_latest_session_capture_coverage
from ...session_scope import normalize_root_domains, scope_payload
from ...project_config import validate_config


router = APIRouter()

# Stop events are in-process signals — not persisted, not shared across workers.
SESSION_ANALYSIS_STOP_EVENTS: dict[str, threading.Event] = {}
SESSION_ANALYSIS_LOCK = threading.Lock()  # guards SESSION_ANALYSIS_STOP_EVENTS only


class SessionAnalyzeRequest(BaseModel):
    options: dict[str, Any] = Field(default_factory=dict)


class SessionUpdateRequest(BaseModel):
    # All fields optional → partial update. name-only keeps the original rename contract.
    name: str | None = Field(default=None, max_length=120)
    rootDomains: list[str] | None = None
    includeSubdomains: bool | None = None
    captureConfig: dict | None = None
    overrideKeys: list[str] | None = None


class SessionBulkDeleteRequest(BaseModel):
    sessionIds: list[str] = Field(default_factory=list)


SESSION_ANALYZE_DEFAULT_OPTIONS: dict[str, Any] = {
    "run_mode": "advanced",
    "analysis_type": "comprehensive",
    "include_sourcemap": True,
    "resolve_urls": True,
    "use_rep_endpoints": True,
    "use_rep_secrets": True,
    "use_jsluice_endpoints": False,
    "use_jsluice_secrets": False,
    "include_reconstructed_sources": True,
    "continue_on_error": True,
    "max_files_to_analyze": None,
    "max_failures": None,
    "per_file_timeout_ms": None,
    "retry_attempts": 0,
}

SESSION_ANALYZE_ALLOWED_TYPES = {"comprehensive", "jsluice"}


def now_iso() -> str:
    return datetime.utcnow().isoformat()


def first_present(options: dict[str, Any], keys: list[str], default: Any = None) -> Any:
    for key in keys:
        if key in options:
            return options[key]
    return default


def parse_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def parse_optional_int(value: Any, minimum: int, maximum: int) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except Exception:
        return None
    if parsed <= 0:
        return None
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


def normalize_session_analysis_options(raw_options: dict[str, Any] | None) -> dict[str, Any]:
    source = raw_options if isinstance(raw_options, dict) else {}
    defaults = dict(SESSION_ANALYZE_DEFAULT_OPTIONS)

    run_mode = str(
        first_present(source, ["run_mode", "runMode"], defaults["run_mode"])
    ).strip().lower()
    if run_mode not in {"quick", "advanced"}:
        run_mode = defaults["run_mode"]

    analysis_type = str(
        first_present(source, ["analysis_type", "analysisType"], defaults["analysis_type"])
    ).strip().lower()
    if analysis_type not in SESSION_ANALYZE_ALLOWED_TYPES:
        analysis_type = defaults["analysis_type"]

    normalized = {
        "run_mode": run_mode,
        "analysis_type": analysis_type,
        "include_sourcemap": parse_bool(
            first_present(source, ["include_sourcemap", "includeSourcemap", "includeSourceMap"]),
            defaults["include_sourcemap"],
        ),
        "resolve_urls": parse_bool(
            first_present(source, ["resolve_urls", "resolveUrls"]),
            defaults["resolve_urls"],
        ),
        "use_rep_endpoints": parse_bool(
            first_present(source, ["use_rep_endpoints", "useRepEndpoints"]),
            defaults["use_rep_endpoints"],
        ),
        "use_rep_secrets": parse_bool(
            first_present(source, ["use_rep_secrets", "useRepSecrets"]),
            defaults["use_rep_secrets"],
        ),
        "use_jsluice_endpoints": parse_bool(
            first_present(source, ["use_jsluice_endpoints", "useJsluiceEndpoints"]),
            defaults["use_jsluice_endpoints"],
        ),
        "use_jsluice_secrets": parse_bool(
            first_present(source, ["use_jsluice_secrets", "useJsluiceSecrets"]),
            defaults["use_jsluice_secrets"],
        ),
        "include_reconstructed_sources": parse_bool(
            first_present(source, ["include_reconstructed_sources", "includeReconstructedSources"]),
            defaults["include_reconstructed_sources"],
        ),
        "continue_on_error": parse_bool(
            first_present(source, ["continue_on_error", "continueOnError"]),
            defaults["continue_on_error"],
        ),
        "max_files_to_analyze": parse_optional_int(
            first_present(source, ["max_files_to_analyze", "maxFilesToAnalyze"]),
            minimum=1,
            maximum=20000,
        ),
        "max_failures": parse_optional_int(
            first_present(source, ["max_failures", "maxFailures"]),
            minimum=1,
            maximum=5000,
        ),
        "per_file_timeout_ms": parse_optional_int(
            first_present(source, ["per_file_timeout_ms", "perFileTimeoutMs"]),
            minimum=250,
            maximum=120000,
        ),
        "retry_attempts": parse_optional_int(
            first_present(source, ["retry_attempts", "retryAttempts"], defaults["retry_attempts"]),
            minimum=1,
            maximum=5,
        )
        or 0,
    }

    if normalized["analysis_type"] == "jsluice":
        normalized["use_rep_endpoints"] = False
        normalized["use_rep_secrets"] = False
        normalized["use_jsluice_endpoints"] = True
        normalized["use_jsluice_secrets"] = True

    submitted_at = first_present(source, ["submitted_at", "submittedAt"], None)
    if isinstance(submitted_at, str) and submitted_at.strip():
        normalized["submitted_at"] = submitted_at.strip()
    else:
        normalized["submitted_at"] = now_iso()
    return normalized


def apply_session_file_limit(files: list[DbFile], options: dict[str, Any]) -> list[DbFile]:
    max_files = parse_optional_int(options.get("max_files_to_analyze"), minimum=1, maximum=20000)
    if not max_files:
        return files
    return files[:max_files]


def build_session_job_state(session_id: str, files: list[DbFile], options: dict[str, Any] | None = None) -> dict[str, Any]:
    timestamp = now_iso()
    normalized_options = normalize_session_analysis_options(options)
    job_files = [
        {
            "fileId": str(file.id),
            "url": file.url,
            "status": "queued",
            "error": None,
            "updatedAt": timestamp,
        }
        for file in files
    ]
    return {
        "sessionId": session_id,
        "jobStatus": "queued",
        "cancelRequested": False,
        "cancelRequestedAt": None,
        "cancelledAt": None,
        "startedAt": timestamp,
        "finishedAt": None,
        "counts": {
            "total": len(job_files),
            "queued": len(job_files),
            "analyzing": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
        },
        "summary": {
            "analyzed": 0,
            "failed": 0,
            "cancelled": 0,
            "failures": [],
        },
        "options": normalized_options,
        "files": job_files,
    }


def recalculate_job_counts(job: dict[str, Any]) -> None:
    files = job.get("files", [])
    counts = {
        "total": len(files),
        "queued": 0,
        "analyzing": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
    }
    for file_entry in files:
        status = str(file_entry.get("status") or "").lower()
        if status in counts:
            counts[status] += 1
    job["counts"] = counts


def get_job_snapshot(session_id: str, db_session) -> dict[str, Any] | None:
    row = (
        db_session.query(DbJob)
        .filter(DbJob.job_type == "session_analysis", DbJob.session_id == session_id)
        .order_by(DbJob.created_at.desc())
        .first()
    )
    if not row:
        return None
    return dict(row.state_json or {})


def update_job_file_status(session_id: str, file_id: str, status: str, error: str | None = None, db_session=None) -> None:
    if db_session is None:
        return
    row = (
        db_session.query(DbJob)
        .filter(DbJob.job_type == "session_analysis", DbJob.session_id == session_id)
        .order_by(DbJob.created_at.desc())
        .first()
    )
    if not row:
        return
    state = dict(row.state_json or {})
    for file_entry in state.get("files", []):
        if file_entry.get("fileId") == file_id:
            file_entry["status"] = status
            file_entry["error"] = error
            file_entry["updatedAt"] = now_iso()
            break
    recalculate_job_counts(state)
    row.state_json = state
    db_session.commit()


def finalize_job(session_id: str, result: dict[str, Any], status: str = "completed", error: str | None = None, db_session=None) -> None:
    if db_session is None:
        return
    row = (
        db_session.query(DbJob)
        .filter(DbJob.job_type == "session_analysis", DbJob.session_id == session_id)
        .order_by(DbJob.created_at.desc())
        .first()
    )
    if not row:
        return
    state = dict(row.state_json or {})
    recalculate_job_counts(state)
    state["jobStatus"] = status
    if status == "cancelled":
        state["cancelledAt"] = now_iso()
    state["finishedAt"] = now_iso()
    state["summary"] = {
        "analyzed": int(result.get("analyzed") or 0),
        "failed": int(result.get("failed") or 0),
        "cancelled": int(result.get("cancelledFiles") or 0),
        "failures": result.get("failures") or [],
    }
    if error:
        state["error"] = error
    row.status = status
    row.finished_at = datetime.utcnow()
    row.error = error
    row.state_json = state
    db_session.commit()


def is_job_cancellation_requested(session_id: str, db_session=None) -> bool:
    if db_session is None:
        # Fall back to stop event check only
        with SESSION_ANALYSIS_LOCK:
            ev = SESSION_ANALYSIS_STOP_EVENTS.get(session_id)
            return ev.is_set() if ev else False
    row = (
        db_session.query(DbJob)
        .filter(DbJob.job_type == "session_analysis", DbJob.session_id == session_id)
        .order_by(DbJob.created_at.desc())
        .first()
    )
    if not row:
        return False
    return bool(row.cancel_requested)


def mark_queued_files_as_cancelled(session_id: str, db_session=None) -> int:
    if db_session is None:
        return 0
    row = (
        db_session.query(DbJob)
        .filter(DbJob.job_type == "session_analysis", DbJob.session_id == session_id)
        .order_by(DbJob.created_at.desc())
        .first()
    )
    if not row:
        return 0
    state = dict(row.state_json or {})
    cancelled = 0
    for file_entry in state.get("files", []):
        if file_entry.get("status") == "queued":
            file_entry["status"] = "cancelled"
            file_entry["error"] = "Analysis stopped by user"
            file_entry["updatedAt"] = now_iso()
            cancelled += 1
    recalculate_job_counts(state)
    row.state_json = state
    db_session.commit()
    return cancelled


@router.get("/api/sessions")
def list_sessions(db: Session = Depends(get_db)):
    rows = (
        db.query(
            DbSession,
            func.count(func.distinct(DbFile.content_hash)).label("file_count"),
            func.coalesce(
                func.sum(
                    case(
                        (func.lower(DbFileAnalysis.status) == "completed", 1),
                        else_=0,
                    )
                ),
                0,
            ).label("analysis_completed"),
            func.coalesce(
                func.sum(
                    case(
                        (func.lower(DbFileAnalysis.status) == "failed", 1),
                        else_=0,
                    )
                ),
                0,
            ).label("analysis_failed"),
        )
        .outerjoin(DbFile, DbFile.session_id == DbSession.id)
        .outerjoin(DbFileAnalysis, DbFileAnalysis.file_id == DbFile.id)
        .group_by(DbSession.id)
        .order_by(DbSession.created_at.desc())
        .all()
    )

    return [
        {
            "id": str(session.id),
            "name": session.name,
            "createdAt": session.created_at.isoformat(),
            "source": session.source,
            "version": session.version,
            **scope_payload(session),
            "projectId": str(session.project_id) if session.project_id else None,
            "overrideKeys": list(session.override_keys or []),
            "captureConfig": session.capture_config,
            "fileCount": int(file_count or 0),
            "analysisSummary": {
                "completed": int(analysis_completed or 0),
                "failed": int(analysis_failed or 0),
                "performed": (int(analysis_completed or 0) + int(analysis_failed or 0)) > 0,
            },
            "captureCoverage": get_latest_session_capture_coverage(str(session.id), db),
            "hasOpenApiSpec": (
                Path(os.environ.get("STORAGE_PATH", "storage"))
                / "sessions"
                / str(session.id)
                / "openapi.yaml"
            ).exists(),
        }
        for session, file_count, analysis_completed, analysis_failed in rows
    ]


@router.get("/api/sessions/{session_id}/openapi")
def download_session_openapi(session_id: str):
    """
    Stream the Vespasian-generated OpenAPI 3.0 YAML spec for a session.

    Returns 404 if no spec has been generated.

    FastAPI FileResponse: https://fastapi.tiangolo.com/advanced/custom-response/#fileresponse
    """
    from fastapi.responses import FileResponse

    spec_path = (
        Path(os.environ.get("STORAGE_PATH", "storage"))
        / "sessions"
        / session_id
        / "openapi.yaml"
    )
    if not spec_path.exists():
        raise HTTPException(status_code=404, detail="No OpenAPI spec for this session.")

    short_id = session_id[:8] if len(session_id) >= 8 else session_id
    return FileResponse(
        path=str(spec_path),
        media_type="application/yaml",
        filename=f"openapi-{short_id}.yaml",
    )


@router.get("/api/sessions/{session_id}/files")
def list_session_files(session_id: str, dedupe: bool = True, db: Session = Depends(get_db)):
    session_uuid = safe_uuid(session_id)
    session = db.query(DbSession).filter(DbSession.id == session_uuid).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    files = (
        db.query(DbFile)
        .filter(DbFile.session_id == session_uuid)
        .order_by(DbFile.captured_at.desc())
        .all()
    )

    file_ids = [file.id for file in files]
    analysis_rows = {}
    source_map_rows = {}
    if file_ids:
        rows = db.query(DbFileAnalysis).filter(DbFileAnalysis.file_id.in_(file_ids)).all()
        analysis_rows = {row.file_id: row for row in rows}
        source_map_entries = db.query(DbSourceMap).filter(DbSourceMap.file_id.in_(file_ids)).all()
        source_map_rows = {row.file_id: row for row in source_map_entries}

    if dedupe:
        files = dedupe_files_by_hash(files, analysis_rows)

    results = []
    for file in files:
        analysis_row = analysis_rows.get(file.id)
        analysis_data = analysis_row.analysis if analysis_row and isinstance(analysis_row.analysis, dict) else {}
        endpoints = analysis_data.get("endpoints", []) if isinstance(analysis_data, dict) else []
        secrets = analysis_data.get("secrets", []) if isinstance(analysis_data, dict) else []
        dependencies = analysis_data.get("dependencies", []) if isinstance(analysis_data, dict) else []
        reconstructed = analysis_data.get("reconstructed_files", []) if isinstance(analysis_data, dict) else []

        results.append(
            {
                "id": str(file.id),
                "url": file.url,
                "contentHash": file.content_hash,
                "contentType": file.content_type,
                "contentEncoding": file.content_encoding,
                "contentLength": file.content_length,
                "capturedAt": file.captured_at.isoformat(),
                "contentPurged": bool(file.content_purged),
                "contentPurgedAt": file.content_purged_at.isoformat() if file.content_purged_at else None,
                "purgeReason": file.purge_reason,
                "analysisStatus": analysis_row.status if analysis_row else "not_analyzed",
                "analysisUpdatedAt": analysis_row.updated_at.isoformat() if analysis_row and analysis_row.updated_at else None,
                "analysisError": analysis_row.error if analysis_row and analysis_row.error else None,
                "analysisStats": analysis_row.stats if analysis_row and analysis_row.stats else {},
                "analysisExtractors": analysis_row.extractors_used if analysis_row and analysis_row.extractors_used else [],
                "analysisCounts": {
                    "endpoints": len(endpoints) if isinstance(endpoints, list) else 0,
                    "secrets": len(secrets) if isinstance(secrets, list) else 0,
                    "dependencies": len(dependencies) if isinstance(dependencies, list) else 0,
                    "reconstructedFiles": len(reconstructed) if isinstance(reconstructed, list) else 0,
                },
                "sourceMap": serialize_sourcemap_state(source_map_rows.get(file.id)),
            }
        )
    return results


@router.get("/api/sessions/{session_id}/sourcemap-validation")
def get_session_sourcemap_validation(session_id: str, dedupe: bool = True, db: Session = Depends(get_db)):
    session_uuid = safe_uuid(session_id)
    session = db.query(DbSession).filter(DbSession.id == session_uuid).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    files = (
        db.query(DbFile)
        .filter(DbFile.session_id == session_uuid)
        .order_by(DbFile.captured_at.desc())
        .all()
    )
    if dedupe:
        files = dedupe_files_by_hash(files, {})

    file_ids = [file.id for file in files]
    source_map_rows: dict[Any, DbSourceMap] = {}
    if file_ids:
        source_map_entries = db.query(DbSourceMap).filter(DbSourceMap.file_id.in_(file_ids)).all()
        source_map_rows = {row.file_id: row for row in source_map_entries}

    matrix_rows: list[dict[str, Any]] = []
    validation_inputs: list[dict[str, Any]] = []
    for file in files:
        if not is_probable_javascript_file(file):
            continue

        source_map = source_map_rows.get(file.id)
        validation = derive_validation_state(source_map) if source_map else {
            "detected": False,
            "fetched": False,
            "http_status": None,
            "content_type": None,
            "json_valid": None,
            "processed": False,
            "candidate_source": None,
            "selected_candidate": None,
            "failure_class": None,
            "updated_at": None,
        }
        validation_inputs.append(validation)
        matrix_rows.append(
            {
                "fileId": str(file.id),
                "url": file.url,
                "capturedAt": file.captured_at.isoformat() if file.captured_at else None,
                "contentType": file.content_type,
                "contentLength": file.content_length,
                "sourceMapId": str(source_map.id) if source_map else None,
                "processingStatus": source_map.processing_status if source_map else "none",
                "processingError": source_map.processing_error if source_map else None,
                "detectedMapUrl": source_map.detected_map_url if source_map else None,
                "validation": validation,
            }
        )

    summary = summarize_validation(validation_inputs)
    return {
        "sessionId": str(session.id),
        "sessionName": session.name,
        "dedupe": bool(dedupe),
        "summary": summary,
        "files": matrix_rows,
    }


@router.delete("/api/sessions/{session_id}")
def delete_session(session_id: str, db: Session = Depends(get_db)):
    session_uuid = safe_uuid(session_id)
    session = db.query(DbSession).filter(DbSession.id == session_uuid).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    file_count = len(session.files)
    file_ids = [file.id for file in session.files]
    storage_dir = Path(settings.storage_path) / "sessions" / str(session.id)

    try:
        if file_ids:
            db.query(DbDependency).filter(DbDependency.file_id.in_(file_ids)).delete(synchronize_session=False)
            db.query(DbSourceMap).filter(DbSourceMap.file_id.in_(file_ids)).delete(synchronize_session=False)
            db.query(DbFileAnalysis).filter(DbFileAnalysis.file_id.in_(file_ids)).delete(synchronize_session=False)
            db.query(DbFile).filter(DbFile.id.in_(file_ids)).delete(synchronize_session=False)

        # Clean up orphaned analysis rows that might still reference session_id.
        db.query(DbFileAnalysis).filter(DbFileAnalysis.session_id == session_uuid).delete(synchronize_session=False)
        db.query(DbSession).filter(DbSession.id == session_uuid).delete(synchronize_session=False)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to delete session: {exc}")

    deleted_storage_dir = False
    if storage_dir.exists() and storage_dir.is_dir():
        try:
            shutil.rmtree(storage_dir)
            deleted_storage_dir = True
        except Exception:
            deleted_storage_dir = False

    return {
        "success": True,
        "sessionId": str(session_uuid),
        "deletedFiles": file_count,
        "deletedStorageDir": deleted_storage_dir,
    }


@router.post("/api/sessions/bulk-delete")
def bulk_delete_sessions(request: SessionBulkDeleteRequest, db: Session = Depends(get_db)):
    unique_ids = list(dict.fromkeys(request.sessionIds or []))
    if not unique_ids:
        raise HTTPException(status_code=400, detail="No session ids provided")

    deleted: list[str] = []
    failed: list[dict[str, str]] = []
    for session_id in unique_ids:
        try:
            delete_session(session_id, db)
            deleted.append(session_id)
        except HTTPException as exc:
            failed.append({"sessionId": session_id, "error": str(exc.detail)})
        except Exception as exc:
            db.rollback()
            failed.append({"sessionId": session_id, "error": str(exc)})

    return {
        "success": len(failed) == 0,
        "requested": len(unique_ids),
        "deleted": deleted,
        "failed": failed,
    }


@router.patch("/api/sessions/{session_id}")
def update_session(session_id: str, request: SessionUpdateRequest, db: Session = Depends(get_db)):
    """Partial update of a session: rename and/or edit its scope (root domains +
    include-subdomains). Only the fields present in the body are changed."""
    session_uuid = safe_uuid(session_id)
    session = db.query(DbSession).filter(DbSession.id == session_uuid).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if request.name is not None:
        name = request.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Session name cannot be empty")
        if len(name) > 120:
            raise HTTPException(status_code=400, detail="Session name is too long")
        session.name = name
    if request.rootDomains is not None:
        session.root_domains = normalize_root_domains(request.rootDomains)
    if request.includeSubdomains is not None:
        session.include_subdomains = bool(request.includeSubdomains)
    if request.captureConfig is not None:
        try:
            validate_config(request.captureConfig, partial=True)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid captureConfig: {exc}")
        session.capture_config = request.captureConfig
    if request.overrideKeys is not None:
        session.override_keys = list(request.overrideKeys)

    db.commit()
    db.refresh(session)

    return {
        "success": True,
        "id": str(session.id),
        "name": session.name,
        "createdAt": session.created_at.isoformat(),
        "source": session.source,
        "version": session.version,
        **scope_payload(session),
        "projectId": str(session.project_id) if session.project_id else None,
        "overrideKeys": list(session.override_keys or []),
        "captureConfig": session.capture_config,
    }


@router.post("/api/sessions/{session_id}/analyze")
def analyze_session_files(
    session_id: str,
    request: SessionAnalyzeRequest,
    db: Session = Depends(get_db)
):
    session_uuid = safe_uuid(session_id)
    options = normalize_session_analysis_options(request.options or {})
    result = execute_session_analysis(db=db, session_uuid=session_uuid, options=options)

    return result


@router.post("/api/sessions/{session_id}/analyze/start")
def start_session_analysis(session_id: str, request: SessionAnalyzeRequest, db: Session = Depends(get_db)):
    session_uuid = safe_uuid(session_id)
    options = normalize_session_analysis_options(request.options or {})
    files = apply_session_file_limit(get_session_files_for_analysis(db, session_uuid), options)
    session_id_str = str(session_uuid)

    # Check for already-running job
    existing_row = (
        db.query(DbJob)
        .filter(DbJob.job_type == "session_analysis", DbJob.session_id == session_id_str)
        .order_by(DbJob.created_at.desc())
        .first()
    )
    if existing_row and existing_row.status in {"queued", "running", "cancelling"}:
        return {
            "success": True,
            "started": False,
            "message": "Session analysis is already running",
            "job": dict(existing_row.state_json or {}),
        }

    initial_state = build_session_job_state(session_id_str, files, options=options)
    db_job = DbJob(
        job_type="session_analysis",
        session_id=session_id_str,
        status="queued",
        state_json=initial_state,
    )
    db.add(db_job)
    with SESSION_ANALYSIS_LOCK:
        SESSION_ANALYSIS_STOP_EVENTS[session_id_str] = threading.Event()
    db.commit()
    snapshot = dict(db_job.state_json)

    worker_session_factory = sessionmaker(autocommit=False, autoflush=False, bind=db.bind)
    thread = threading.Thread(
        target=run_session_analysis_worker,
        args=(session_uuid, options, worker_session_factory),
        daemon=True,
    )
    thread.start()

    return {
        "success": True,
        "started": True,
        "message": "Session analysis started",
        "job": snapshot,
    }


@router.post("/api/sessions/{session_id}/analyze/stop")
def stop_session_analysis(session_id: str, db: Session = Depends(get_db)):
    session_uuid = safe_uuid(session_id)
    session = db.query(DbSession).filter(DbSession.id == session_uuid).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session_id_str = str(session_uuid)

    row = (
        db.query(DbJob)
        .filter(DbJob.job_type == "session_analysis", DbJob.session_id == session_id_str)
        .order_by(DbJob.created_at.desc())
        .first()
    )
    if not row:
        return {
            "success": True,
            "stopRequested": False,
            "message": "No active session analysis job found",
            "job": None,
        }
    if row.status in {"completed", "failed", "cancelled", "idle"}:
        return {
            "success": True,
            "stopRequested": False,
            "message": f"Session analysis already finished with status '{row.status}'",
            "job": dict(row.state_json or {}),
        }

    state = dict(row.state_json or {})
    state["cancelRequested"] = True
    if not state.get("cancelRequestedAt"):
        state["cancelRequestedAt"] = now_iso()
    state["jobStatus"] = "cancelling"
    row.cancel_requested = True
    row.cancel_requested_at = datetime.utcnow()
    row.status = "cancelling"
    row.state_json = state
    db.commit()
    with SESSION_ANALYSIS_LOCK:
        ev = SESSION_ANALYSIS_STOP_EVENTS.get(session_id_str)
        if ev:
            ev.set()
    return {
        "success": True,
        "stopRequested": True,
        "message": "Stop requested. Session analysis will halt after the current file.",
        "job": dict(state),
    }


@router.get("/api/sessions/{session_id}/analyze/progress")
def get_session_analysis_progress(session_id: str, db: Session = Depends(get_db)):
    session_uuid = safe_uuid(session_id)
    session = db.query(DbSession).filter(DbSession.id == session_uuid).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    session_id_str = str(session_uuid)
    snapshot = get_job_snapshot(session_id_str, db)
    if snapshot:
        return {
            "success": True,
            "sessionId": session_id_str,
            "job": snapshot,
        }

    files = get_session_files_for_analysis(db, session_uuid, raise_if_empty=False)
    analysis_rows = {}
    if files:
        file_ids = [file.id for file in files]
        rows = db.query(DbFileAnalysis).filter(DbFileAnalysis.file_id.in_(file_ids)).all()
        analysis_rows = {row.file_id: row for row in rows}

    file_entries: list[dict[str, Any]] = []
    counts = {
        "total": len(files),
        "queued": 0,
        "analyzing": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
    }
    analyzed_count = 0
    failed_count = 0

    for file in files:
        row = analysis_rows.get(file.id)
        status = str(row.status).lower() if row and row.status else "queued"
        if status not in counts:
            status = "queued"
        counts[status] += 1
        if status == "completed":
            analyzed_count += 1
        if status == "failed":
            failed_count += 1
        file_entries.append(
            {
                "fileId": str(file.id),
                "url": file.url,
                "status": status,
                "error": row.error if row and row.error else None,
                "updatedAt": row.updated_at.isoformat() if row and row.updated_at else None,
            }
        )

    idle_job = {
        "sessionId": session_id_str,
        "jobStatus": "idle",
        "startedAt": None,
        "finishedAt": None,
        "counts": counts,
        "summary": {
            "analyzed": analyzed_count,
            "failed": failed_count,
            "cancelled": 0,
            "failures": [],
        },
        "options": None,
        "files": file_entries,
    }

    return {
        "success": True,
        "sessionId": session_id_str,
        "job": idle_job,
    }


def get_session_files_for_analysis(db: Session, session_uuid: uuid.UUID, raise_if_empty: bool = True) -> list[DbFile]:
    files = (
        db.query(DbFile)
        .filter(DbFile.session_id == session_uuid)
        .order_by(DbFile.captured_at.desc())
        .all()
    )
    if raise_if_empty and not files:
        raise HTTPException(status_code=404, detail="No files found in session")
    return files


def execute_session_analysis(
    db: Session,
    session_uuid: uuid.UUID,
    options: dict[str, Any],
    progress_callback: Callable[[DbFile, str, str | None], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    on_cancel: Callable[[], int] | None = None,
) -> dict[str, Any]:
    options = normalize_session_analysis_options(options)
    session = db.query(DbSession).filter(DbSession.id == session_uuid).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    files = apply_session_file_limit(get_session_files_for_analysis(db, session_uuid), options)
    extractor = ComprehensiveExtractor()
    analyzed = 0
    failed = 0
    failures: list[dict[str, str]] = []
    cancelled_files = 0
    continue_on_error = bool(options.get("continue_on_error", True))
    max_failures = parse_optional_int(options.get("max_failures"), minimum=1, maximum=5000)

    for index, file in enumerate(files):
        if should_cancel and should_cancel():
            if on_cancel:
                cancelled_files = on_cancel()
            return {
                "success": True,
                "sessionId": str(session.id),
                "totalFiles": len(files),
                "analyzed": analyzed,
                "failed": failed,
                "cancelled": True,
                "cancelledFiles": cancelled_files,
                "failures": failures,
            }

        if progress_callback:
            progress_callback(file, "analyzing", None)

        # Race-safe get-or-create in "analyzing" state (file_analyses.file_id is
        # UNIQUE): concurrent analysis of one file can't raise an unhandled
        # UniqueViolation that would abort this whole session-analysis run.
        row = get_or_create_analyzing_file_analysis(db, file.id, file.session_id)
        db.commit()

        content_path = Path(file.stored_path)
        if not content_path.exists():
            row.status = "failed"
            row.error = "Stored file content not found"
            row.updated_at = datetime.utcnow()
            db.commit()
            failed += 1
            failures.append({"fileId": str(file.id), "error": row.error})
            if progress_callback:
                progress_callback(file, "failed", row.error)
            if not continue_on_error:
                pending = files[index + 1 :]
                for queued_file in pending:
                    if progress_callback:
                        progress_callback(queued_file, "cancelled", "Stopped by fail-fast setting after first error")
                cancelled_files += len(pending)
                break
            if max_failures and failed >= max_failures:
                pending = files[index + 1 :]
                for queued_file in pending:
                    if progress_callback:
                        progress_callback(queued_file, "cancelled", "Stopped after reaching max failure threshold")
                cancelled_files += len(pending)
                break
            continue

        try:
            content = content_path.read_text(encoding="utf-8")
            metadata = {
                **(file.file_metadata or {}),
                "url": file.url,
                "contentType": file.content_type or "application/javascript",
                "analysisTimestamp": datetime.utcnow().isoformat(),
            }
            result = extractor.extract_all(content, metadata, options=options)
            row.status = "completed"
            row.analysis = result.get("analysis", {})
            row.stats = result.get("stats", {})
            row.extractors_used = result.get("extractors_used", [])
            row.error = None
            row.updated_at = datetime.utcnow()
            db.commit()
            analyzed += 1
            if progress_callback:
                progress_callback(file, "completed", None)
        except Exception as exc:
            row.status = "failed"
            row.error = str(exc)
            row.updated_at = datetime.utcnow()
            db.commit()
            failed += 1
            failures.append({"fileId": str(file.id), "error": row.error})
            if progress_callback:
                progress_callback(file, "failed", row.error)
            if not continue_on_error:
                pending = files[index + 1 :]
                for queued_file in pending:
                    if progress_callback:
                        progress_callback(queued_file, "cancelled", "Stopped by fail-fast setting after first error")
                cancelled_files += len(pending)
                break
            if max_failures and failed >= max_failures:
                pending = files[index + 1 :]
                for queued_file in pending:
                    if progress_callback:
                        progress_callback(queued_file, "cancelled", "Stopped after reaching max failure threshold")
                cancelled_files += len(pending)
                break

    return {
        "success": failed == 0,
        "sessionId": str(session.id),
        "totalFiles": len(files),
        "analyzed": analyzed,
        "failed": failed,
        "cancelled": False,
        "cancelledFiles": cancelled_files,
        "failures": failures,
    }


def run_session_analysis_worker(
    session_uuid: uuid.UUID,
    options: dict[str, Any],
    worker_session_factory: sessionmaker,
) -> None:
    session_id_str = str(session_uuid)
    db = worker_session_factory()
    try:
        row = (
            db.query(DbJob)
            .filter(DbJob.job_type == "session_analysis", DbJob.session_id == session_id_str)
            .order_by(DbJob.created_at.desc())
            .first()
        )
        if row:
            state = dict(row.state_json or {})
            if row.cancel_requested:
                state["jobStatus"] = "cancelling"
            else:
                state["jobStatus"] = "running"
            state["startedAt"] = state.get("startedAt") or now_iso()
            row.status = state["jobStatus"]
            row.started_at = datetime.utcnow()
            row.state_json = state
            db.commit()

        if is_job_cancellation_requested(session_id_str, db):
            cancelled_files = mark_queued_files_as_cancelled(session_id_str, db)
            finalize_job(
                session_id_str,
                {"analyzed": 0, "failed": 0, "cancelledFiles": cancelled_files, "failures": []},
                status="cancelled",
                db_session=db,
            )
            return

        result = execute_session_analysis(
            db=db,
            session_uuid=session_uuid,
            options=options,
            progress_callback=lambda file, status, error: update_job_file_status(
                session_id_str, str(file.id), status, error, db
            ),
            should_cancel=lambda: is_job_cancellation_requested(session_id_str, db),
            on_cancel=lambda: mark_queued_files_as_cancelled(session_id_str, db),
        )
        status = "cancelled" if result.get("cancelled") else "completed"
        finalize_job(session_id_str, result, status=status, db_session=db)
    except HTTPException as exc:
        if is_job_cancellation_requested(session_id_str, db):
            cancelled = mark_queued_files_as_cancelled(session_id_str, db)
            finalize_job(
                session_id_str,
                {"analyzed": 0, "failed": 0, "cancelledFiles": cancelled, "failures": []},
                status="cancelled",
                db_session=db,
            )
        else:
            finalize_job(
                session_id_str,
                {"analyzed": 0, "failed": 0, "cancelledFiles": 0, "failures": []},
                status="failed",
                error=str(exc.detail),
                db_session=db,
            )
    except Exception as exc:
        if is_job_cancellation_requested(session_id_str, db):
            cancelled = mark_queued_files_as_cancelled(session_id_str, db)
            finalize_job(
                session_id_str,
                {"analyzed": 0, "failed": 0, "cancelledFiles": cancelled, "failures": []},
                status="cancelled",
                db_session=db,
            )
        else:
            finalize_job(
                session_id_str,
                {"analyzed": 0, "failed": 0, "cancelledFiles": 0, "failures": []},
                status="failed",
                error=str(exc),
                db_session=db,
            )
    finally:
        db.close()


def safe_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid session id")


def is_probable_javascript_file(file: DbFile) -> bool:
    content_type = str(file.content_type or "").lower()
    if "javascript" in content_type or "ecmascript" in content_type:
        return True
    url = str(file.url or "").lower()
    return ".js" in url or ".mjs" in url or ".jsx" in url


def dedupe_files_by_hash(
    files: list[DbFile],
    analysis_rows: dict[uuid.UUID, DbFileAnalysis],
) -> list[DbFile]:
    status_rank = {
        "completed": 4,
        "analyzing": 3,
        "failed": 2,
        "not_analyzed": 1,
    }

    def key_for(file: DbFile) -> str:
        if file.content_hash:
            return file.content_hash
        return f"{file.url}|{file.content_length}"

    def rank_for(file: DbFile) -> int:
        status = (analysis_rows.get(file.id).status if analysis_rows.get(file.id) else "not_analyzed") or "not_analyzed"
        return status_rank.get(str(status).lower(), 0)

    selected: dict[str, DbFile] = {}
    for file in files:
        key = key_for(file)
        current = selected.get(key)
        if current is None:
            selected[key] = file
            continue

        current_rank = rank_for(current)
        file_rank = rank_for(file)
        if file_rank > current_rank:
            selected[key] = file
            continue
        if file_rank < current_rank:
            continue

        current_time = current.captured_at or datetime.min
        file_time = file.captured_at or datetime.min
        if file_time > current_time:
            selected[key] = file

    return sorted(selected.values(), key=lambda item: item.captured_at or datetime.min, reverse=True)


def serialize_sourcemap_state(source_map: DbSourceMap | None) -> dict[str, Any] | None:
    if not source_map:
        return None

    validation = derive_validation_state(source_map)
    return {
        "id": str(source_map.id),
        "fileId": str(source_map.file_id),
        "mapUrl": source_map.map_url,
        "detectedMapUrl": source_map.detected_map_url,
        "parsed": source_map.parsed,
        "processingStatus": source_map.processing_status,
        "processingError": source_map.processing_error,
        "reconstructedFilesCount": source_map.reconstructed_files_count,
        "processedAt": source_map.processed_at.isoformat() if source_map.processed_at else None,
        "contentPurged": bool(source_map.content_purged),
        "contentPurgedAt": source_map.content_purged_at.isoformat() if source_map.content_purged_at else None,
        "purgeReason": source_map.purge_reason,
        "validation": validation,
    }


@router.get("/api/sessions/{session_id}/analysis/summary")
def get_session_analysis_summary(session_id: str, db: Session = Depends(get_db)):
    """
    Get rolled up analysis summary for a session, including deduplicated secrets.
    Implements B-025 - Secret Rollup by Type+Value with Source Provenance.
    """
    # Validate session exists
    session = db.query(DbSession).filter(DbSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get all file analyses for this session
    analyses = (
        db.query(DbFileAnalysis)
        .join(DbFile, DbFileAnalysis.file_id == DbFile.id)
        .filter(DbFile.session_id == session_id)
        .filter(DbFileAnalysis.status == "completed")
        .all()
    )

    if not analyses:
        return {
            "session_id": session_id,
            "session_name": session.name,
            "summary": {
                "total_files": 0,
                "analyzed_files": 0,
                "secrets": {
                    "total_unique_secrets": 0,
                    "total_occurrences": 0,
                    "total_files_with_secrets": 0,
                    "by_type": {},
                    "by_confidence": {},
                    "by_extractor": {},
                    "risk_distribution": {
                        "high_risk": 0,
                        "medium_risk": 0,
                        "low_risk": 0
                    },
                    "deduplication_ratio": 0
                },
                "endpoints": {
                    "total_unique_endpoints": 0,
                    "total_occurrences": 0
                },
                "dependencies": {
                    "total_dependencies": 0
                }
            },
            "secrets_rollup": []
        }

    # Get file information for each analysis
    file_map = {analysis.file_id: analysis.file for analysis in analyses}

    # Prepare data for rollup service
    analysis_data = []
    total_dependencies = 0

    for analysis in analyses:
        file_info = file_map.get(analysis.file_id)
        analysis_dict = {
            "id": str(analysis.file_id),
            "file": {
                "url": file_info.url if file_info else "unknown",
                "content_hash": file_info.content_hash if file_info else None
            },
            "analysis": analysis.analysis or {}
        }
        analysis_data.append(analysis_dict)

        # Count dependencies for summary
        dependencies = analysis.analysis.get("dependencies", []) if analysis.analysis else []
        total_dependencies += len(dependencies)

    # Use rollup service to deduplicate secrets
    rollup_service = SecretRollupService()
    secret_rollup_result = rollup_service.rollup_secrets(analysis_data)
    endpoint_rollup = summarize_endpoint_rollup(analysis_data)

    # Build comprehensive summary
    total_files = db.query(DbFile).filter(DbFile.session_id == session_id).count()

    summary = {
        "total_files": total_files,
        "analyzed_files": len(analyses),
        "secrets": secret_rollup_result["summary"],
        "endpoints": endpoint_rollup,
        "dependencies": {
            "total_dependencies": total_dependencies
        }
    }

    return {
        "session_id": session_id,
        "session_name": session.name,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "summary": summary,
        "secrets_rollup": secret_rollup_result["secrets"]
    }


def normalize_endpoint_identity(endpoint: dict[str, Any]) -> str | None:
    if not isinstance(endpoint, dict):
        return None

    raw_url = str(endpoint.get("url") or endpoint.get("endpoint") or "").strip()
    if not raw_url:
        return None

    try:
        parts = urlsplit(raw_url)
        query_pairs = parse_qsl(parts.query, keep_blank_values=True)
        normalized_query = urlencode(sorted(query_pairs))
        normalized_url = urlunsplit((
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path,
            normalized_query,
            "",
        ))
    except Exception:
        normalized_url = raw_url

    method = str(endpoint.get("method") or "").strip().upper()
    endpoint_type = str(endpoint.get("type") or "").strip().lower()
    return f"{method}|{endpoint_type}|{normalized_url}"


def summarize_endpoint_rollup(file_analyses: list[dict[str, Any]]) -> dict[str, int]:
    unique_identities: set[str] = set()
    total_occurrences = 0

    for analysis in file_analyses:
        endpoints = analysis.get("analysis", {}).get("endpoints", [])
        if not isinstance(endpoints, list):
            continue
        for endpoint in endpoints:
            identity = normalize_endpoint_identity(endpoint)
            if not identity:
                continue
            unique_identities.add(identity)
            total_occurrences += 1

    return {
        "total_unique_endpoints": len(unique_identities),
        "total_occurrences": total_occurrences,
    }


def compute_global_stats(db: Session) -> dict[str, int]:
    session_count = int(db.query(func.count(DbSession.id)).scalar() or 0)
    file_count = int(db.query(func.count(DbFile.id)).scalar() or 0)
    analyses = (
        db.query(DbFileAnalysis)
        .join(DbFile, DbFileAnalysis.file_id == DbFile.id)
        .filter(DbFileAnalysis.status == "completed")
        .all()
    )

    analysis_data = []
    for analysis in analyses:
        file_info = analysis.file
        analysis_data.append(
            {
                "id": str(analysis.file_id),
                "file": {
                    "url": file_info.url if file_info else "unknown",
                    "content_hash": file_info.content_hash if file_info else None,
                },
                "analysis": analysis.analysis or {},
            }
        )

    endpoint_summary = summarize_endpoint_rollup(analysis_data)
    secret_summary = (
        SecretRollupService().rollup_secrets(analysis_data)["summary"]
        if analysis_data
        else {"total_unique_secrets": 0}
    )

    return {
        "sessions": session_count,
        "files": file_count,
        "endpoints": int(endpoint_summary["total_unique_endpoints"]),
        "secrets": int(secret_summary["total_unique_secrets"]),
    }


@router.get("/api/stats")
def get_global_stats(db: Session = Depends(get_db)):
    return compute_global_stats(db)
