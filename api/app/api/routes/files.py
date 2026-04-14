from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
import uuid

from ...db import get_db
from ...models import File as DbFile
from ...models import Dependency as DbDependency
from ...models import FileAnalysis as DbFileAnalysis
from ...models import SourceMap as DbSourceMap
from ...services.comprehensive_extractor import ComprehensiveExtractor
from ...services.native_sourcemap_processor import NativeSourceMapProcessor
from ...services.async_utils import run_coroutine_sync
from ...services.auth_context import redact_file_metadata_for_output
from ...services.sourcemap_validation import derive_validation_state


router = APIRouter()


class FileAnalyzeRequest(BaseModel):
    options: dict[str, Any] = Field(default_factory=dict)


class FileBulkDeleteRequest(BaseModel):
    fileIds: list[str] = Field(default_factory=list)


@router.post("/api/files/bulk-delete")
def bulk_delete_files(request: FileBulkDeleteRequest, db: Session = Depends(get_db)):
    unique_ids = list(dict.fromkeys(request.fileIds or []))
    if not unique_ids:
        raise HTTPException(status_code=400, detail="No file ids provided")

    deleted: list[str] = []
    failed: list[dict[str, str]] = []
    for file_id in unique_ids:
        try:
            delete_file(file_id, db)
            deleted.append(file_id)
        except HTTPException as exc:
            failed.append({"fileId": file_id, "error": str(exc.detail)})
        except Exception as exc:
            db.rollback()
            failed.append({"fileId": file_id, "error": str(exc)})

    return {
        "success": len(failed) == 0,
        "requested": len(unique_ids),
        "deleted": deleted,
        "failed": failed,
    }


@router.get("/api/files/{file_id}")
def get_file(file_id: str, db: Session = Depends(get_db)):
    file_uuid = safe_uuid(file_id)
    file = db.query(DbFile).filter(DbFile.id == file_uuid).first()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    return {
        "id": str(file.id),
        "sessionId": str(file.session_id),
        "url": file.url,
        "contentHash": file.content_hash,
        "contentType": file.content_type,
        "contentEncoding": file.content_encoding,
        "contentLength": file.content_length,
        "capturedAt": file.captured_at.isoformat(),
        "metadata": redact_file_metadata_for_output(file.file_metadata),
        "contentPurged": bool(file.content_purged),
        "contentPurgedAt": file.content_purged_at.isoformat() if file.content_purged_at else None,
        "purgeReason": file.purge_reason,
        "sourceMap": serialize_sourcemap_state(file.source_map),
    }


@router.get("/api/files/{file_id}/content")
def get_file_content(file_id: str, db: Session = Depends(get_db)):
    file_uuid = safe_uuid(file_id)
    file = db.query(DbFile).filter(DbFile.id == file_uuid).first()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    if file.content_purged:
        raise HTTPException(status_code=410, detail=build_purged_file_content_detail(file))
    file_path = Path(file.stored_path)
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Stored file content not found")
    return FileResponse(file.stored_path, media_type=file.content_type or "application/javascript")


@router.get("/api/files/{file_id}/sourcemap-content")
def get_file_sourcemap_content(file_id: str, db: Session = Depends(get_db)):
    file_uuid = safe_uuid(file_id)
    file = db.query(DbFile).filter(DbFile.id == file_uuid).first()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    source_map = file.source_map
    if source_map and source_map.content_purged:
        raise HTTPException(status_code=410, detail=build_purged_sourcemap_content_detail(file, source_map))
    map_path = None
    if source_map and source_map.stored_path:
        map_path = source_map.stored_path
    elif file.map_path:
        map_path = file.map_path

    if not map_path:
        raise HTTPException(status_code=404, detail="Source map not available for this file")

    path = Path(map_path)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Stored source map content not found")

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read source map content: {exc}")

    return {
        "fileId": str(file.id),
        "sessionId": str(file.session_id),
        "sourceMap": serialize_sourcemap_state(source_map),
        "content": content,
    }


@router.get("/api/files/{file_id}/dependencies")
def get_file_dependencies(file_id: str, recursive: bool = False, db: Session = Depends(get_db)):
    file_uuid = safe_uuid(file_id)
    file = db.query(DbFile).filter(DbFile.id == file_uuid).first()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    def deps_for_file(file_obj):
        rows = db.query(DbDependency).filter(DbDependency.file_id == file_obj.id).all()
        return [
            {
                "url": dep.dep_url,
                "resolvedUrl": dep.resolved_url,
                "type": dep.dep_type,
            }
            for dep in rows
        ]

    if not recursive:
        return deps_for_file(file)

    visited_urls = set()
    queue = [file]
    results = []

    while queue:
        current = queue.pop(0)
        current_deps = deps_for_file(current)
        for dep in current_deps:
            key = dep.get("resolvedUrl") or dep.get("url")
            if key in visited_urls:
                continue
            visited_urls.add(key)
            results.append(dep)
            if dep.get("resolvedUrl"):
                next_file = (
                    db.query(DbFile)
                    .filter(DbFile.session_id == file.session_id)
                    .filter(DbFile.url == dep["resolvedUrl"])
                    .first()
                )
                if next_file:
                    queue.append(next_file)

    return results


@router.post("/api/files/{file_id}/analyze")
def analyze_file(file_id: str, request: FileAnalyzeRequest, db: Session = Depends(get_db)):
    file_uuid = safe_uuid(file_id)
    file = db.query(DbFile).filter(DbFile.id == file_uuid).first()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    row = db.query(DbFileAnalysis).filter(DbFileAnalysis.file_id == file.id).first()
    if not row:
        row = DbFileAnalysis(
            file_id=file.id,
            session_id=file.session_id,
            status="analyzing",
            analysis={},
            stats={},
            extractors_used=[],
            error=None,
        )
        db.add(row)
    else:
        row.status = "analyzing"
        row.error = None
        row.updated_at = datetime.utcnow()
    db.commit()

    content_path = Path(file.stored_path)
    if not content_path.exists():
        row.status = "failed"
        row.error = "Stored file content not found"
        row.updated_at = datetime.utcnow()
        db.commit()
        raise HTTPException(status_code=404, detail=row.error)

    try:
        content = content_path.read_text(encoding="utf-8")
    except Exception as exc:
        row.status = "failed"
        row.error = f"Failed to read stored file: {exc}"
        row.updated_at = datetime.utcnow()
        db.commit()
        raise HTTPException(status_code=500, detail=row.error)

    metadata = {
        **(file.file_metadata or {}),
        "url": file.url,
        "contentType": file.content_type or "application/javascript",
        "analysisTimestamp": datetime.utcnow().isoformat(),
    }

    options = request.options or {}
    extractor = ComprehensiveExtractor()

    started_at = datetime.utcnow()
    try:
        results = extractor.extract_all(content, metadata, options=options)
    except Exception as exc:
        row.status = "failed"
        row.error = str(exc)
        row.updated_at = datetime.utcnow()
        db.commit()
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}")

    processing_time = int((datetime.utcnow() - started_at).total_seconds() * 1000)

    row.status = "completed"
    row.analysis = results.get("analysis", {})
    row.stats = results.get("stats", {})
    row.extractors_used = results.get("extractors_used", [])
    row.error = None
    row.updated_at = datetime.utcnow()

    db.commit()

    return {
        "success": True,
        "fileId": str(file.id),
        "sessionId": str(file.session_id),
        "status": row.status,
        "analysis": results,
        "processing_time_ms": processing_time,
        "extractors_used": results.get("extractors_used", []),
    }


@router.get("/api/files/{file_id}/analysis")
def get_file_analysis(file_id: str, db: Session = Depends(get_db)):
    file_uuid = safe_uuid(file_id)
    file = db.query(DbFile).filter(DbFile.id == file_uuid).first()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    row = db.query(DbFileAnalysis).filter(DbFileAnalysis.file_id == file.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Analysis not found for this file")

    return {
        "fileId": str(file.id),
        "sessionId": str(file.session_id),
        "status": row.status,
        "error": row.error,
        "analysis": row.analysis,
        "extractors_used": row.extractors_used,
        "stats": row.stats,
        "createdAt": row.created_at.isoformat(),
        "updatedAt": row.updated_at.isoformat(),
    }


@router.get("/api/files/{file_id}/reconstructed-sources")
def get_file_reconstructed_sources(file_id: str, db: Session = Depends(get_db)):
    """Get reconstructed source files from sourcemap processing"""
    file_uuid = safe_uuid(file_id)
    file = db.query(DbFile).filter(DbFile.id == file_uuid).first()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    # Check if file has a sourcemap
    source_map = file.source_map
    if not source_map:
        raise HTTPException(status_code=404, detail="No sourcemap found for this file")
    if source_map.content_purged:
        raise HTTPException(status_code=410, detail=build_purged_sourcemap_content_detail(file, source_map))
    
    # Check if sourcemap processing was successful
    completed_statuses = {"completed", "completed_limited"}
    if source_map.processing_status not in completed_statuses or not source_map.parsed:
        raise HTTPException(
            status_code=404, 
            detail=f"Sourcemap processing not completed (status: {source_map.processing_status})"
        )
    
    if source_map.reconstructed_files_count == 0:
        return {"files": [], "stats": {"totalFiles": 0, "totalSize": 0}}
    
    # Get sourcemap content
    map_path = None
    if source_map.stored_path:
        map_path = source_map.stored_path
    elif file.map_path:
        map_path = file.map_path
        
    if not map_path:
        raise HTTPException(status_code=404, detail="Sourcemap content not available")
    
    sourcemap_path = Path(map_path)
    if not sourcemap_path.exists() or not sourcemap_path.is_file():
        raise HTTPException(status_code=404, detail="Stored sourcemap content not found")
    
    try:
        sourcemap_content = sourcemap_path.read_text(encoding="utf-8")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read sourcemap content: {exc}")
    
    # Re-process sourcemap to get reconstructed files
    try:
        processor = NativeSourceMapProcessor()

        # Use process_sourcemap_from_content since we already have the content
        result = run_coroutine_sync(processor.process_sourcemap_from_content(sourcemap_content))
        
        if not result.get("success"):
            error_msg = result.get("error", "Unknown processing error")
            raise HTTPException(status_code=500, detail=f"Failed to process sourcemap: {error_msg}")
        
        files = result.get("files", [])
        stats = result.get("stats", {})
        
        # Format response to match expected API contract
        formatted_files = []
        for file_info in files:
            formatted_files.append({
                "path": file_info.get("path", ""),
                "content": file_info.get("content", ""),
                "size": file_info.get("size", 0),
                "type": file_info.get("type", "unknown"),
                "originalPath": file_info.get("original_path", ""),
                "sourceIndex": file_info.get("source_index", 0)
            })
        
        return {
            "files": formatted_files,
            "stats": {
                "totalFiles": len(formatted_files),
                "totalSize": sum(f["size"] for f in formatted_files),
                "jsFiles": len([f for f in formatted_files if f["type"] == "javascript"]),
                "otherFiles": len([f for f in formatted_files if f["type"] != "javascript"])
            },
            "sourcemap": {
                "id": str(source_map.id),
                "fileId": str(file.id),
                "reconstructedFilesCount": source_map.reconstructed_files_count,
                "processedAt": source_map.processed_at.isoformat() if source_map.processed_at else None
            }
        }
        
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error reconstructing sources: {exc}")


@router.delete("/api/files/{file_id}")
def delete_file(file_id: str, db: Session = Depends(get_db)):
    file_uuid = safe_uuid(file_id)
    file = db.query(DbFile).filter(DbFile.id == file_uuid).first()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    stored_path = file.stored_path
    source_map_rows = db.query(DbSourceMap).filter(DbSourceMap.file_id == file.id).all()
    map_paths = set()
    if file.map_path:
        map_paths.add(file.map_path)
    for row in source_map_rows:
        if row.stored_path:
            map_paths.add(row.stored_path)

    shared_stored_path = False
    if stored_path:
        shared_stored_path = (
            db.query(DbFile)
            .filter(DbFile.id != file.id)
            .filter(DbFile.stored_path == stored_path)
            .first()
            is not None
        )

    shared_map_paths = {}
    for map_path in map_paths:
        used_by_files = (
            db.query(DbFile)
            .filter(DbFile.id != file.id)
            .filter(DbFile.map_path == map_path)
            .first()
            is not None
        )
        used_by_sourcemaps = (
            db.query(DbSourceMap)
            .filter(DbSourceMap.file_id != file.id)
            .filter(DbSourceMap.stored_path == map_path)
            .first()
            is not None
        )
        shared_map_paths[map_path] = used_by_files or used_by_sourcemaps

    session_id = str(file.session_id)
    try:
        db.query(DbDependency).filter(DbDependency.file_id == file.id).delete(synchronize_session=False)
        db.query(DbFileAnalysis).filter(DbFileAnalysis.file_id == file.id).delete(synchronize_session=False)
        db.query(DbSourceMap).filter(DbSourceMap.file_id == file.id).delete(synchronize_session=False)
        db.delete(file)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {exc}")

    deleted_paths: list[str] = []
    if stored_path and not shared_stored_path and delete_path_if_exists(stored_path):
        deleted_paths.append(stored_path)
    for map_path, is_shared in shared_map_paths.items():
        if not is_shared and delete_path_if_exists(map_path):
            deleted_paths.append(map_path)

    return {
        "success": True,
        "fileId": str(file_uuid),
        "sessionId": session_id,
        "deletedArtifactsCount": len(deleted_paths),
    }


def safe_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid file id")


def delete_path_if_exists(path_value: str) -> bool:
    path = Path(path_value)
    if not path.exists() or not path.is_file():
        return False

    try:
        path.unlink()
        return True
    except Exception:
        return False


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


def build_purged_file_content_detail(file: DbFile) -> dict[str, Any]:
    return {
        "message": "Stored file content was purged by retention policy",
        "artifactType": "file_content",
        "fileId": str(file.id),
        "sessionId": str(file.session_id),
        "contentPurged": True,
        "contentPurgedAt": file.content_purged_at.isoformat() if file.content_purged_at else None,
        "purgeReason": file.purge_reason or "retention_ttl_expired",
    }


def build_purged_sourcemap_content_detail(file: DbFile, source_map: DbSourceMap) -> dict[str, Any]:
    return {
        "message": "Stored sourcemap content was purged by retention policy",
        "artifactType": "sourcemap_content",
        "fileId": str(file.id),
        "sessionId": str(file.session_id),
        "sourceMapId": str(source_map.id),
        "contentPurged": True,
        "contentPurgedAt": source_map.content_purged_at.isoformat() if source_map.content_purged_at else None,
        "purgeReason": source_map.purge_reason or "retention_ttl_expired",
    }
