from datetime import datetime
import uuid
from typing import Any
import logging
import re
import time
import httpx

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import Session as DbSession
from ...models import Project as DbProject
from ...models import File as DbFile
from ...models import Dependency as DbDependency
from ...models import SourceMap as DbSourceMap
from ...models import FileAnalysis as DbFileAnalysis
from ...config import settings
from ...session_scope import derive_root_domains, normalize_root_domains
from ...project_config import validate_config
from ...services.comprehensive_extractor import ComprehensiveExtractor
from ...services.file_analysis_persistence import get_or_create_analyzing_file_analysis
from ...services.storage import StorageService
from ...services.native_sourcemap_processor import NativeSourceMapProcessor
from ...services.async_utils import run_coroutine_sync
from ...services.security_utils import SecurityValidator
from ...services.analysis_triggers import SmartAnalysisTriggers
from ...services.auth_context import (
    AUTH_REPLAY_ELIGIBLE_ERROR_CLASSES,
    get_auth_replay_headers,
    sanitize_captured_auth_context,
)
from ...services.sourcemap_validation import (
    build_initial_validation_state,
    derive_validation_state,
    extract_error_class,
    infer_http_status,
    merge_validation_state,
)


router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/api/analysis/smart-triggers")
def get_smart_trigger_config():
    """Get current smart analysis trigger configuration"""
    triggers = SmartAnalysisTriggers()
    return {
        "config": triggers.get_trigger_summary(),
        "description": {
            "min_file_size": "Minimum file size in bytes to trigger analysis",
            "with_sourcemaps": "Auto-analyze files with successful sourcemap processing", 
            "api_pattern_threshold": "Minimum API patterns to trigger analysis",
            "secret_pattern_threshold": "Minimum secret patterns to trigger analysis",
            "minified_js_threshold": "Ratio of long lines to detect minified JS"
        }
    }

SOURCEMAP_TIMEOUT_SECONDS = max(1, int(settings.sourcemap_processing_timeout_seconds))
SOURCEMAP_MAX_SOURCEMAP_SIZE = max(1, int(settings.sourcemap_max_size_bytes))
SOURCEMAP_MAX_RECONSTRUCTED_FILES = max(1, int(settings.sourcemap_max_reconstructed_files))
SOURCEMAP_MAX_FETCH_RETRIES = 3
SOURCEMAP_RETRY_BASE_SECONDS = 0.5

# Cap per-finding string fields before persisting analysis. The parameter/secret extractors
# embed a raw code slice in each finding's ``context``; on large minified bundles this balloons
# the analysis jsonb to multiple MB, which both bloats the DB and (over Docker's network) can
# drop the connection mid-write. A few hundred chars is plenty of context for triage, and the
# finding fingerprint is keyed on kind|value|file|line — never context — so capping is safe.
ANALYSIS_MAX_FIELD_CHARS = 500


def cap_analysis_payload(analysis: Any) -> Any:
    """Truncate oversized string fields in each finding so the stored analysis stays small."""
    if not isinstance(analysis, dict):
        return analysis
    for value in analysis.values():
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            for field_name, field_value in list(item.items()):
                if isinstance(field_value, str) and len(field_value) > ANALYSIS_MAX_FIELD_CHARS:
                    item[field_name] = field_value[:ANALYSIS_MAX_FIELD_CHARS] + "…[truncated]"
    return analysis


class DependencyIn(BaseModel):
    url: str
    type: str | None = None
    resolvedUrl: str | None = None


class FileIn(BaseModel):
    model_config = ConfigDict(extra="allow")

    url: str
    contentHash: str
    sessionId: str
    capturedAt: str | None = None
    contentType: str | None = None
    contentEncoding: str | None = None
    contentLength: int
    content: str
    sourceMapUrl: str | None = None
    sourceMapContent: dict | None = None
    headers: dict[str, str] | None = Field(default=None, description="HTTP response headers")
    authContext: dict[str, Any] | None = Field(default=None, description="Captured auth context for sourcemap replay")
    dependencies: list[DependencyIn] = Field(default_factory=list)


class IngestionPayload(BaseModel):
    metadata: dict[str, Any] | None = None
    files: list[FileIn]


@router.post("/api/save-files")
def save_files(payload: IngestionPayload, db: Session = Depends(get_db)):
    if not payload.files:
        raise HTTPException(status_code=400, detail="No files provided")

    session_id = payload.metadata.get("sessionId") if payload.metadata else None
    if not session_id:
        session_id = payload.files[0].sessionId

    session_uuid = safe_uuid(session_id)
    db_session = db.query(DbSession).filter(DbSession.id == session_uuid).first()
    if not db_session:
        # Seed scope + project membership + config snapshot on create only (later
        # appends never re-bind). The client sends the already-resolved effective
        # config; the backend stores it as-is (snapshot-on-create; single-user).
        meta = payload.metadata or {}
        include_subdomains = meta.get("includeSubdomains")
        explicit_roots = normalize_root_domains(meta.get("rootDomains") or [])
        project_id = safe_project_uuid(meta.get("projectId"))
        # A stale popup cache can reference a since-deleted project. Binding a
        # non-existent project_id would hit the FK and 500, dropping the whole
        # capture batch (and a 4xx would too — the extension drops 4xx as
        # non-retriable). Coerce an unknown project to standalone instead.
        if project_id is not None and not db.query(DbProject.id).filter(DbProject.id == project_id).first():
            logger.warning("save-files: unknown projectId %s; saving session %s as standalone", project_id, session_uuid)
            project_id = None
        capture_config = meta.get("captureConfig")
        if capture_config is not None:
            try:
                validate_config(capture_config, partial=True)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"invalid captureConfig: {exc}")
        override_keys = meta.get("overrideKeys")
        if not isinstance(override_keys, list):
            override_keys = []
        db_session = DbSession(
            id=session_uuid,
            root_domains=explicit_roots or derive_root_domains([f.url for f in payload.files]),
            include_subdomains=True if include_subdomains is None else bool(include_subdomains),
            project_id=project_id,
            capture_config=capture_config,
            override_keys=override_keys,
        )
        db.add(db_session)
        try:
            db.commit()
        except IntegrityError:
            # A concurrent first-time writer created this session id first (both
            # SELECT-missed above, both INSERT). Adopt the row they committed
            # rather than 500 on ``sessions_pkey``. Mirrors triage.py's commit-catch.
            db.rollback()
            db_session = db.query(DbSession).filter(DbSession.id == session_uuid).first()
            if db_session is None:
                raise
        else:
            db.refresh(db_session)

    storage = StorageService()
    file_ids = []
    file_results = []  # Track per-file results including sourcemap status
    manual_analysis_requested = bool(payload.metadata.get("performAnalysis")) if payload.metadata else False
    analysis_disabled = bool(payload.metadata.get("disableAnalysis")) if payload.metadata else False
    analysis_options = payload.metadata.get("analysisOptions") if payload.metadata else None
    if not isinstance(analysis_options, dict):
        analysis_options = {}
    
    # Initialize smart triggers and extractor
    smart_triggers = SmartAnalysisTriggers()
    extractor = None
    analysis_completed = 0
    analysis_failed = 0

    for file_in in payload.files:
        validate_ingestion_file_input(file_in)
        logger.info(f"Processing file upload: {file_in.url}")
        stored_path = storage.write_file(session_uuid, file_in.contentHash, file_in.content)
        map_path = None

        if file_in.sourceMapContent is not None:
            map_path = storage.write_map(session_uuid, file_in.contentHash, json_dump(file_in.sourceMapContent))

        metadata = file_in.model_dump()
        metadata.pop("content", None)
        metadata.pop("sourceMapContent", None)
        auth_context = sanitize_captured_auth_context(file_in.authContext, file_in.url)
        if auth_context:
            metadata["authContext"] = auth_context
        else:
            metadata.pop("authContext", None)

        captured_at = parse_datetime(file_in.capturedAt) or datetime.utcnow()

        # Always attempt sourcemap detection for JavaScript files
        detected_sourcemap_url = None
        sourcemap_detection_status = "not_attempted"
        
        # Check if this is a JavaScript file that could have a sourcemap
        is_js_file = bool(
            file_in.url.endswith(('.js', '.mjs', '.jsx')) or 
            (file_in.contentType and 'javascript' in file_in.contentType.lower())
        )
        
        if is_js_file:
            sourcemap_detection_status = "attempted"
            try:
                if not file_in.sourceMapUrl:
                    # No explicit sourcemap URL provided, try to detect from headers and content
                    detected_sourcemap_url, detection_method = detect_sourcemap_url(file_in.content, file_in.url, file_in.headers)
                    if detected_sourcemap_url:
                        sourcemap_detection_status = "detected"
                        logger.info(f"Auto-detected sourcemap URL for {file_in.url} via {detection_method}: {detected_sourcemap_url}")
                    else:
                        sourcemap_detection_status = "none_found"
                        detection_method = "none"
                else:
                    # Sourcemap URL was explicitly provided
                    detected_sourcemap_url = file_in.sourceMapUrl
                    sourcemap_detection_status = "provided"
                    detection_method = "provided"
                    logger.info(f"Using provided sourcemap URL for {file_in.url}: {detected_sourcemap_url}")
            except Exception as e:
                logger.error(f"Error during sourcemap detection for {file_in.url}: {e}")
                sourcemap_detection_status = "error"
                detected_sourcemap_url = None
                detection_method = "error"

        # Store sourcemap detection result in metadata
        sourcemap_detection_metadata = {
            'status': sourcemap_detection_status,
            'detected_url': detected_sourcemap_url,
            'attempted_at': datetime.utcnow().isoformat()
        }
        
        # Add detection method for JS files
        if is_js_file and 'detection_method' in locals():
            sourcemap_detection_metadata['detection_method'] = detection_method
            
        metadata['sourcemap_detection'] = sourcemap_detection_metadata

        # Check if file already exists for this session+hash combination (idempotency)
        existing_file = db.query(DbFile).filter(
            DbFile.session_id == session_uuid,
            DbFile.content_hash == file_in.contentHash
        ).first()
        
        if existing_file:
            # File already exists - return existing record for idempotent behavior
            db_file = existing_file
            logger.info(f"File already exists for session {session_uuid} + hash {file_in.contentHash[:8]}... - using existing record {db_file.id}")
        else:
            # Create new file record. Wrap the INSERT in a SAVEPOINT so that a
            # concurrent writer racing us on the same (session_id, content_hash) —
            # both SELECT-miss above, both INSERT — degrades to idempotent reuse
            # instead of an unhandled IntegrityError that poisons this whole batch
            # transaction (which aborts the recon crawl). Mirrors triage.py's
            # commit-catch, but nested so the surrounding per-batch work survives.
            db_file = DbFile(
                session_id=session_uuid,
                url=file_in.url,
                content_hash=file_in.contentHash,
                content_type=file_in.contentType,
                content_encoding=file_in.contentEncoding,
                content_length=file_in.contentLength,
                captured_at=captured_at,
                file_metadata=metadata,
                stored_path=stored_path,
                map_path=map_path
            )
            try:
                with db.begin_nested():
                    db.add(db_file)
                    db.flush()
            except IntegrityError:
                # Lost the insert race. Adopt the row the winner committed and treat
                # this file as pre-existing so the dependency (:278), sourcemap (:289)
                # and analysis ("existing") gates below stay idempotent — no duplicate
                # child rows. Do NOT use ON CONFLICT DO NOTHING: downstream needs
                # db_file.id, which DO NOTHING would not return.
                db_file = db.query(DbFile).filter(
                    DbFile.session_id == session_uuid,
                    DbFile.content_hash == file_in.contentHash,
                ).first()
                if db_file is None:
                    # No winner visible (row vanished between the failed INSERT and
                    # this re-SELECT — no delete path exists today). Don't proceed
                    # with a dead object.
                    raise
                existing_file = db_file
                logger.info(
                    "Concurrent insert race for session %s + hash %s...; adopting winning file %s",
                    session_uuid,
                    file_in.contentHash[:8],
                    db_file.id,
                )

        # Handle dependencies - only add if this is a new file
        if not existing_file and file_in.dependencies:
            for dep in file_in.dependencies:
                db.add(DbDependency(
                    file_id=db_file.id,
                    dep_url=dep.url,
                    resolved_url=dep.resolvedUrl,
                    dep_type=dep.type
                ))

        # Handle SourceMap - only create if this is a new file or no sourcemap exists
        final_sourcemap_url = file_in.sourceMapUrl or detected_sourcemap_url
        sourcemap_record = db_file.source_map  # Check for existing sourcemap
        
        if (final_sourcemap_url or map_path) and not sourcemap_record:
            initial_validation_state = build_initial_validation_state(
                detected=bool(final_sourcemap_url or map_path),
                fetched=True if file_in.sourceMapContent is not None else None,
                http_status=200 if file_in.sourceMapContent is not None else None,
                content_type="application/json" if file_in.sourceMapContent is not None else None,
                json_valid=None,
                processed=False,
                candidate_source=sourcemap_detection_metadata.get("detection_method"),
                selected_candidate=final_sourcemap_url or detected_sourcemap_url,
            )
            # Create new SourceMap record only if none exists
            sourcemap_record = DbSourceMap(
                file_id=db_file.id,
                map_url=final_sourcemap_url,
                stored_path=map_path,
                parsed=False,
                detected_map_url=detected_sourcemap_url,
                processing_status="pending",
                reconstructed_files_count=0,
                validation_state=initial_validation_state,
            )
            db.add(sourcemap_record)
            db.flush()  # Get the sourcemap record ID

            # Prefer uploaded sourcemap content (often auth-gated/ephemeral when fetched by URL).
            if file_in.sourceMapContent is not None:
                process_sourcemap_content_safely(
                    sourcemap_record=sourcemap_record,
                    sourcemap_content=json_dump(file_in.sourceMapContent),
                    js_url=file_in.url,
                    db=db,
                )
            elif final_sourcemap_url:
                process_sourcemap_safely(
                    sourcemap_record,
                    final_sourcemap_url,
                    db,
                    js_url=file_in.url,
                    auth_context=auth_context,
                )
        elif sourcemap_record:
            sourcemap_record.validation_state = merge_validation_state(
                sourcemap_record.validation_state,
                {
                    "detected": bool(final_sourcemap_url or sourcemap_record.detected_map_url or sourcemap_record.map_url),
                    "selected_candidate": final_sourcemap_url or sourcemap_record.detected_map_url or sourcemap_record.map_url,
                    "candidate_source": sourcemap_detection_metadata.get("detection_method"),
                },
            )

        analysis_result = {
            "requested": manual_analysis_requested,
            "status": "skipped",
            "error": None,
        }
        
        # Determine if analysis should be triggered
        if analysis_disabled:
            trigger_decision = {
                "trigger": False,
                "reason": "disabled_by_ingestion_metadata",
                "criteria_met": [],
            }
        else:
            sourcemap_processing_status = sourcemap_record.processing_status if sourcemap_record else None
            trigger_decision = smart_triggers.should_trigger_analysis(
                content=file_in.content,
                file_metadata=db_file.file_metadata or {},
                sourcemap_status=sourcemap_processing_status,
                manual_analysis_requested=manual_analysis_requested
            )
        
        should_analyze = trigger_decision["trigger"]
        
        # Initialize extractor only when needed
        if should_analyze and not extractor:
            extractor = ComprehensiveExtractor()
        
        # Handle analysis - run if triggered, or use existing if available
        if should_analyze and extractor:
            # Check if analysis already exists for this file
            if existing_file and db_file.analysis_result:
                # File already analyzed - return existing analysis status
                analysis_result = {
                    "requested": True,
                    "status": "existing",
                    "error": None,
                    "trigger_reason": "existing_analysis"
                }
                logger.info(f"Using existing analysis for file {db_file.id}")
            else:
                # Run new analysis
                analysis_result = run_ingestion_analysis(
                    db=db,
                    db_file=db_file,
                    file_in=file_in,
                    extractor=extractor,
                    analysis_options=analysis_options,
                )
                # Add trigger information to result
                analysis_result["trigger_reason"] = trigger_decision["reason"]
                analysis_result["trigger_criteria"] = trigger_decision["criteria_met"]
                
                if analysis_result["status"] == "completed":
                    analysis_completed += 1
                else:
                    analysis_failed += 1
        else:
            # Analysis not triggered
            analysis_result["trigger_reason"] = trigger_decision["reason"]
            analysis_result["trigger_criteria"] = trigger_decision.get("criteria_met", [])

        file_ids.append(str(db_file.id))
        
        # Collect file result data for response
        file_result = {
            "fileId": str(db_file.id),
            "url": file_in.url,
            "contentHash": file_in.contentHash,
            "sourceMap": serialize_sourcemap_state(sourcemap_record),
            "analysis": analysis_result,
        }
        file_results.append(file_result)

    db.commit()

    logger.info(
        "Saved %s files for session %s",
        len(file_ids),
        str(db_session.id)
    )

    # Determine overall analysis status
    total_analyzed = analysis_completed + analysis_failed
    if total_analyzed == 0:
        if manual_analysis_requested:
            analysis_status = "skipped" 
        else:
            analysis_status = "smart_skipped"  # No files met smart trigger criteria
    elif analysis_failed == 0:
        analysis_status = "completed"
    elif analysis_completed == 0:
        analysis_status = "failed"
    else:
        analysis_status = "partial_failed"

    return {
        "success": True,
        "sessionId": str(db_session.id),
        "stored": len(file_ids),
        "fileIds": file_ids,
        "files": file_results,
        "analysis": {
            "requested": manual_analysis_requested,
            "status": analysis_status,
            "completed": analysis_completed,
            "failed": analysis_failed,
            "smart_triggers_enabled": settings.smart_analysis_enabled,
        },
    }


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def validate_ingestion_file_input(file_in: FileIn) -> None:
    try:
        SecurityValidator.validate_url(file_in.url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid file url '{file_in.url}': {exc}")

    if file_in.sourceMapUrl:
        try:
            SecurityValidator.validate_url(file_in.sourceMapUrl)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid sourceMapUrl '{file_in.sourceMapUrl}': {exc}")

    try:
        SecurityValidator.validate_js_content(file_in.content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid file content for '{file_in.url}': {exc}")

    if file_in.contentLength < 0:
        raise HTTPException(status_code=422, detail=f"Invalid contentLength for '{file_in.url}': must be >= 0")

    for dep in file_in.dependencies:
        if dep.resolvedUrl:
            try:
                SecurityValidator.validate_url(dep.resolvedUrl)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=f"Invalid dependency resolvedUrl '{dep.resolvedUrl}': {exc}")


def run_ingestion_analysis(
    db: Session,
    db_file: DbFile,
    file_in: FileIn,
    extractor: ComprehensiveExtractor,
    analysis_options: dict[str, Any],
) -> dict[str, Any]:
    analysis_metadata = {
        **(db_file.file_metadata or {}),
        "url": db_file.url,
        "contentType": db_file.content_type or "application/javascript",
        "analysisTimestamp": datetime.utcnow().isoformat(),
    }

    try:
        with db.begin_nested():
            # Race-safe get-or-create in "analyzing" state (file_analyses.file_id is
            # UNIQUE); the helper's own SAVEPOINT nests inside this one and adopts a
            # concurrent writer's row on collision.
            analysis_row = get_or_create_analyzing_file_analysis(db, db_file.id, db_file.session_id)

            results = extractor.extract_all(file_in.content, analysis_metadata, options=analysis_options)

            analysis_row.status = "completed"
            analysis_row.analysis = cap_analysis_payload(results.get("analysis", {}))
            analysis_row.stats = results.get("stats", {})
            analysis_row.extractors_used = results.get("extractors_used", [])
            analysis_row.error = None
            analysis_row.updated_at = datetime.utcnow()
            db.flush()

            return {
                "requested": True,
                "status": "completed",
                "error": None,
                "extractorsUsed": results.get("extractors_used", []),
            }
    except Exception as exc:
        error_message = str(exc)[:1000]

        try:
            with db.begin_nested():
                analysis_row = db.query(DbFileAnalysis).filter(DbFileAnalysis.file_id == db_file.id).first()
                if not analysis_row:
                    analysis_row = DbFileAnalysis(
                        file_id=db_file.id,
                        session_id=db_file.session_id,
                        status="failed",
                        analysis={},
                        stats={},
                        extractors_used=[],
                        error=error_message,
                    )
                    db.add(analysis_row)
                else:
                    analysis_row.status = "failed"
                    analysis_row.error = error_message
                    analysis_row.analysis = analysis_row.analysis if isinstance(analysis_row.analysis, dict) else {}
                    analysis_row.stats = analysis_row.stats if isinstance(analysis_row.stats, dict) else {}
                    analysis_row.extractors_used = analysis_row.extractors_used or []
                    analysis_row.updated_at = datetime.utcnow()
                db.flush()
        except Exception as persist_exc:
            logger.warning(
                "Failed to persist ingestion analysis failure for file %s: %s",
                db_file.id,
                persist_exc,
            )

        return {
            "requested": True,
            "status": "failed",
            "error": error_message,
            "extractorsUsed": [],
        }


def json_dump(value: dict) -> str:
    import json

    return json.dumps(value, indent=2)


def safe_uuid(value: str | None) -> uuid.UUID:
    if not value:
        return uuid.uuid4()
    try:
        return uuid.UUID(value)
    except Exception:
        # Deterministically map legacy/non-UUID session ids.
        return uuid.uuid5(uuid.NAMESPACE_URL, value)


def safe_project_uuid(value):
    """Parse an optional projectId. None/'' -> None; invalid -> HTTP 400."""
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid projectId")


def detect_sourcemap_url(js_content: str, js_url: str, headers: dict[str, str] | None = None) -> tuple[str | None, str]:
    """
    Detect sourcemap URL from HTTP headers or JavaScript content comments.
    
    Priority order:
    1. SourceMap or X-SourceMap response headers (if headers provided)
    2. JavaScript content comment patterns
    
    Returns tuple of (sourcemap_url, detection_method).
    Detection methods: 'header', 'content', 'none'
    """
    try:
        # Check HTTP headers first for sourcemap hints
        if headers:
            # Check for standard SourceMap header (case-insensitive)
            for header_name, header_value in headers.items():
                if header_name.lower() in ("sourcemap", "x-sourcemap"):
                    if header_value and header_value.strip():
                        sourcemap_url = header_value.strip()
                        
                        # Resolve relative URLs
                        if not sourcemap_url.startswith(('http://', 'https://', 'data:')):
                            from urllib.parse import urljoin
                            base_url = js_url.rsplit('/', 1)[0]
                            sourcemap_url = urljoin(base_url + '/', sourcemap_url)
                        
                        logger.info(f"Found sourcemap URL in {header_name} header for {js_url}: {sourcemap_url}")
                        return sourcemap_url, 'header'
        
        # Fallback to content-based detection
        processor = NativeSourceMapProcessor(
            timeout=SOURCEMAP_TIMEOUT_SECONDS,
            max_sourcemap_size_bytes=SOURCEMAP_MAX_SOURCEMAP_SIZE,
            max_reconstructed_files=SOURCEMAP_MAX_RECONSTRUCTED_FILES,
        )
        content_url = processor._extract_sourcemap_url_from_content(js_content, js_url)
        if content_url:
            return content_url, 'content'
        else:
            return None, 'none'
    except Exception as e:
        logger.warning(f"Failed to detect sourcemap URL for {js_url}: {e}")
        return None, 'error'


def process_sourcemap_safely(
    sourcemap_record: DbSourceMap,
    sourcemap_url: str,
    db: Session,
    js_url: str | None = None,
    auth_context: dict[str, Any] | None = None,
) -> None:
    """
    Safely process a sourcemap with timeout and size limits.
    Updates the sourcemap_record with processing results.
    Non-fatal - errors are logged and stored but don't break ingestion.
    """
    from datetime import datetime

    try:
        logger.info(f"Starting sourcemap processing for URL: {sourcemap_url}")
        sourcemap_record.processing_status = "processing"
        sourcemap_record.validation_state = merge_validation_state(
            sourcemap_record.validation_state,
            {
                "detected": True,
                "selected_candidate": sourcemap_url,
                "processed": False,
                "failure_class": None,
            },
        )
        db.flush()

        processor = NativeSourceMapProcessor()
        resolved_js_url = js_url
        resolved_auth_context = auth_context
        if (resolved_js_url is None or resolved_auth_context is None) and sourcemap_record.file_id:
            parent_file = db.query(DbFile).filter(DbFile.id == sourcemap_record.file_id).first()
            if parent_file:
                if resolved_js_url is None:
                    resolved_js_url = parent_file.url
                if resolved_auth_context is None and isinstance(parent_file.file_metadata, dict):
                    candidate_auth_context = parent_file.file_metadata.get("authContext")
                    if isinstance(candidate_auth_context, dict):
                        resolved_auth_context = candidate_auth_context

        # First check sourcemap size before full processing.
        content_length = None
        head_http_status: int | None = None
        head_content_type: str | None = None
        try:
            response = httpx.head(sourcemap_url, timeout=10, follow_redirects=True)
            head_http_status = response.status_code
            head_content_type = response.headers.get("content-type")
            content_length = response.headers.get("content-length")
        except Exception:
            content_length = None
        if content_length:
            try:
                parsed_length = int(content_length)
            except (TypeError, ValueError):
                parsed_length = None
            if parsed_length and parsed_length > SOURCEMAP_MAX_SOURCEMAP_SIZE:
                raise ValueError(f"Sourcemap too large: {parsed_length} bytes > {SOURCEMAP_MAX_SOURCEMAP_SIZE}")

        js_url_for_processor = resolved_js_url or sourcemap_url

        def process_with_retries(
            custom_headers: dict[str, str] | None,
            attempt_label: str,
        ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
            result: dict[str, Any] | None = None
            last_fetch_meta: dict[str, Any] = {}
            for attempt in range(1, SOURCEMAP_MAX_FETCH_RETRIES + 1):
                try:
                    process_kwargs: dict[str, Any] = {}
                    if custom_headers:
                        process_kwargs["custom_headers"] = custom_headers
                    result = run_coroutine_sync(
                        processor.process_sourcemap_from_url(js_url_for_processor, sourcemap_url, **process_kwargs),
                        timeout=SOURCEMAP_TIMEOUT_SECONDS,
                    )
                except TimeoutError:
                    result = {
                        "success": False,
                        "files": [],
                        "error": f"Processing timeout after {SOURCEMAP_TIMEOUT_SECONDS} seconds",
                    }
                current_fetch_meta = getattr(processor, "last_fetch_metadata", None)
                if isinstance(current_fetch_meta, dict) and current_fetch_meta:
                    last_fetch_meta = dict(current_fetch_meta)

                if result.get("success"):
                    break

                error_msg = result.get("error", "Unknown processing error")
                error_class = classify_sourcemap_error(error_msg)
                retriable = is_retriable_sourcemap_error(error_class)
                has_retries_left = attempt < SOURCEMAP_MAX_FETCH_RETRIES
                if retriable and has_retries_left:
                    delay = SOURCEMAP_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
                    logger.warning(
                        "Sourcemap %s transient error for %s (class=%s, attempt=%s/%s); retrying in %.2fs",
                        attempt_label,
                        sourcemap_url,
                        error_class,
                        attempt,
                        SOURCEMAP_MAX_FETCH_RETRIES,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                break
            return result, last_fetch_meta

        result, fetch_meta = process_with_retries(custom_headers=None, attempt_label="direct")
        direct_error_class = classify_sourcemap_error((result or {}).get("error"))
        replay_headers = get_auth_replay_headers(resolved_auth_context, sourcemap_url)
        should_attempt_auth_replay = (
            bool(replay_headers)
            and not (result or {}).get("success")
            and direct_error_class in AUTH_REPLAY_ELIGIBLE_ERROR_CLASSES
        )
        if should_attempt_auth_replay:
            logger.info(
                "Retrying sourcemap fetch with auth context for %s after direct failure class=%s",
                sourcemap_url,
                direct_error_class,
            )
            result, replay_fetch_meta = process_with_retries(
                custom_headers=replay_headers,
                attempt_label="auth_context",
            )
            if replay_fetch_meta:
                fetch_meta = replay_fetch_meta

        if head_http_status is not None and not fetch_meta.get("http_status"):
            fetch_meta["http_status"] = head_http_status
        if head_content_type and not fetch_meta.get("content_type"):
            fetch_meta["content_type"] = head_content_type

        apply_sourcemap_processing_result(
            sourcemap_record,
            result,
            source_label=sourcemap_url,
            fetch_metadata=fetch_meta,
            candidate_source="url_fetch",
        )
    except Exception as e:
        error_msg = str(e)
        error_class = classify_sourcemap_error(error_msg)
        sourcemap_record.processing_status = "failed"
        sourcemap_record.processing_error = format_sourcemap_error(error_class, error_msg)[:1000]
        sourcemap_record.reconstructed_files_count = 0
        sourcemap_record.processed_at = datetime.utcnow()
        sourcemap_record.validation_state = merge_validation_state(
            sourcemap_record.validation_state,
            {
                "detected": True,
                "fetched": False,
                "http_status": infer_http_status(error_class, error_msg),
                "json_valid": False if error_class in {"decode_invalid_json", "decode_content"} else None,
                "processed": False,
                "failure_class": error_class,
                "selected_candidate": sourcemap_url,
                "candidate_source": "url_fetch",
            },
        )
        logger.error(f"Sourcemap processing error for {sourcemap_url}: class={error_class} error={error_msg}")
        
    # Always flush the status update
    db.flush()


def process_sourcemap_content_safely(
    sourcemap_record: DbSourceMap,
    sourcemap_content: str,
    js_url: str,
    db: Session,
) -> None:
    """
    Safely process sourcemap from uploaded content.
    This avoids re-fetching auth-gated sourcemaps that were already captured by the extension.
    """
    from datetime import datetime

    try:
        logger.info("Starting sourcemap processing from uploaded content for JS URL: %s", js_url)
        sourcemap_record.processing_status = "processing"
        sourcemap_record.validation_state = merge_validation_state(
            sourcemap_record.validation_state,
            {
                "detected": True,
                "fetched": True,
                "http_status": 200,
                "content_type": "application/json",
                "processed": False,
                "failure_class": None,
                "selected_candidate": sourcemap_record.detected_map_url or sourcemap_record.map_url,
                "candidate_source": "uploaded_content",
            },
        )
        db.flush()

        content_size = len((sourcemap_content or "").encode("utf-8"))
        if content_size > SOURCEMAP_MAX_SOURCEMAP_SIZE:
            raise ValueError(f"Sourcemap too large: {content_size} bytes > {SOURCEMAP_MAX_SOURCEMAP_SIZE}")

        processor = NativeSourceMapProcessor()
        try:
            result = run_coroutine_sync(
                processor.process_sourcemap_from_content(sourcemap_content, js_url),
                timeout=SOURCEMAP_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            result = {
                "success": False,
                "files": [],
                "error": f"Processing timeout after {SOURCEMAP_TIMEOUT_SECONDS} seconds",
            }

        apply_sourcemap_processing_result(
            sourcemap_record,
            result,
            source_label=js_url,
            fetch_metadata={
                "fetched": True,
                "http_status": 200,
                "content_type": "application/json",
            },
            candidate_source="uploaded_content",
        )
    except Exception as e:
        error_msg = str(e)
        error_class = classify_sourcemap_error(error_msg)
        sourcemap_record.processing_status = "failed"
        sourcemap_record.processing_error = format_sourcemap_error(error_class, error_msg)[:1000]
        sourcemap_record.reconstructed_files_count = 0
        sourcemap_record.processed_at = datetime.utcnow()
        sourcemap_record.validation_state = merge_validation_state(
            sourcemap_record.validation_state,
            {
                "detected": True,
                "fetched": True,
                "http_status": 200,
                "content_type": "application/json",
                "json_valid": False if error_class in {"decode_invalid_json", "decode_content"} else None,
                "processed": False,
                "failure_class": error_class,
                "selected_candidate": sourcemap_record.detected_map_url or sourcemap_record.map_url,
                "candidate_source": "uploaded_content",
            },
        )
        logger.error(
            "Sourcemap content processing error for %s: class=%s error=%s",
            js_url,
            error_class,
            error_msg,
        )

    db.flush()


def apply_sourcemap_processing_result(
    sourcemap_record: DbSourceMap,
    result: dict[str, Any] | None,
    source_label: str,
    fetch_metadata: dict[str, Any] | None = None,
    candidate_source: str | None = None,
) -> None:
    from datetime import datetime

    fetch_metadata = fetch_metadata or {}
    error_msg = result.get("error", "Unknown processing error") if result else "Unknown processing error"
    error_class = classify_sourcemap_error(error_msg)
    processing_success = bool(result and result.get("success"))

    inferred_fetched = fetch_metadata.get("fetched")
    if inferred_fetched is None:
        if processing_success:
            inferred_fetched = True
        elif error_class in {"decode_invalid_json", "decode_content"} or error_class.startswith("fetch_"):
            inferred_fetched = True

    inferred_http_status = fetch_metadata.get("http_status")
    if inferred_http_status is None:
        inferred_http_status = infer_http_status(error_class, error_msg)

    inferred_json_valid: bool | None = None
    if processing_success:
        inferred_json_valid = True
    elif error_class in {"decode_invalid_json", "decode_content"}:
        inferred_json_valid = False

    validation_updates = {
        "detected": True,
        "fetched": inferred_fetched,
        "http_status": inferred_http_status,
        "content_type": fetch_metadata.get("content_type"),
        "json_valid": inferred_json_valid,
        "processed": processing_success,
        "failure_class": None if processing_success else error_class,
        "selected_candidate": sourcemap_record.detected_map_url or sourcemap_record.map_url,
        "candidate_source": candidate_source,
    }

    if result and result.get("success") and result.get("files"):
        file_count = len(result["files"])
        processing_stats = result.get("stats") or {}
        was_truncated = bool(processing_stats.get("truncated"))
        source_candidates = processing_stats.get("sources_with_content")
        if file_count > SOURCEMAP_MAX_RECONSTRUCTED_FILES:
            was_truncated = True
            source_candidates = max(file_count, int(source_candidates or 0))
            file_count = SOURCEMAP_MAX_RECONSTRUCTED_FILES

        sourcemap_record.reconstructed_files_count = file_count
        sourcemap_record.parsed = True
        sourcemap_record.processed_at = datetime.utcnow()
        if was_truncated:
            total_candidates = int(source_candidates or file_count)
            sourcemap_record.processing_status = "completed_limited"
            sourcemap_record.processing_error = format_sourcemap_error(
                "resource_limit",
                (
                    f"Reconstructed files capped at {SOURCEMAP_MAX_RECONSTRUCTED_FILES}; "
                    f"{total_candidates} source files contained embedded content."
                ),
            )[:1000]
            logger.warning(
                "Sourcemap processing completed with limits from %s: %s/%s files retained",
                source_label,
                file_count,
                total_candidates,
            )
        else:
            sourcemap_record.processing_status = "completed"
            sourcemap_record.processing_error = None
            logger.info(
                "Successfully processed sourcemap from %s: %s files reconstructed",
                source_label,
                file_count,
            )
        sourcemap_record.validation_state = merge_validation_state(sourcemap_record.validation_state, validation_updates)
        return

    if result and result.get("success"):
        sourcemap_record.processing_status = "completed"
        sourcemap_record.reconstructed_files_count = 0
        sourcemap_record.parsed = True
        sourcemap_record.processing_error = None
        sourcemap_record.processed_at = datetime.utcnow()
        sourcemap_record.validation_state = merge_validation_state(sourcemap_record.validation_state, validation_updates)
        logger.info("Sourcemap processed from %s but no files reconstructed", source_label)
        return

    sourcemap_record.processing_status = "failed"
    sourcemap_record.processing_error = format_sourcemap_error(error_class, error_msg)[:1000]
    sourcemap_record.reconstructed_files_count = 0
    sourcemap_record.processed_at = datetime.utcnow()
    sourcemap_record.validation_state = merge_validation_state(sourcemap_record.validation_state, validation_updates)
    logger.warning(
        "Sourcemap processing failed from %s: class=%s error=%s",
        source_label,
        error_class,
        error_msg,
    )


def classify_sourcemap_error(error_msg: str | None) -> str:
    message = str(error_msg or "").strip()
    lower = message.lower()
    if not message:
        return "processing_unknown"

    if "timeout" in lower:
        return "processing_timeout"
    if "too large" in lower:
        return "resource_limit"

    http_match = re.search(r"http error fetching source map:\s*(\d{3})", lower)
    if http_match:
        code = int(http_match.group(1))
        if code == 404:
            return "fetch_http_404"
        if code == 401:
            return "fetch_http_401"
        if code == 403:
            return "fetch_http_403"
        if code == 429:
            return "fetch_http_429"
        if 500 <= code <= 599:
            return "fetch_http_5xx"
        return "fetch_http_4xx"

    if "request error fetching source map" in lower or "connection" in lower or "dns" in lower:
        return "fetch_network"
    if "invalid json" in lower or "missing required field" in lower or "must be a json object" in lower:
        return "decode_invalid_json"
    if "base64" in lower or "decode" in lower:
        return "decode_content"
    return "processing_unknown"


def is_retriable_sourcemap_error(error_class: str) -> bool:
    return error_class in {"fetch_http_429", "fetch_http_5xx", "fetch_network", "processing_timeout"}


def format_sourcemap_error(error_class: str, message: str | None) -> str:
    text = str(message or "Unknown processing error").strip()
    return f"[{error_class}] {text}"


def serialize_sourcemap_state(source_map: DbSourceMap | None) -> dict[str, Any] | None:
    """Serialize sourcemap state for API responses."""
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
