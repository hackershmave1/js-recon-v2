from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from ..config import settings
from ..db import SessionLocal
from ..models import File as DbFile
from ..models import SourceMap as DbSourceMap

logger = logging.getLogger(__name__)


@dataclass
class CleanupCandidate:
    path: Path
    content_type: str
    age_days: int


def _resolve_base_path(base_path: str | None = None) -> Path:
    return Path(base_path or settings.storage_path)


def _resolve_ttl_days(value: int | None, fallback: int) -> int:
    ttl_days = fallback if value is None else value
    return max(int(ttl_days), 0)


def _resolve_max_deletions(value: int | None, fallback: int) -> int:
    max_deletions = fallback if value is None else value
    return max(int(max_deletions), 1)


def _age_days_from_mtime(path: Path, now: datetime) -> int:
    modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    age = now - modified_at
    return max(int(age.total_seconds() // 86400), 0)


def _iter_expired_files(
    root: Path,
    subfolder: str,
    ttl_days: int,
    content_type: str,
    now: datetime,
) -> Iterable[CleanupCandidate]:
    if ttl_days <= 0:
        return []

    cutoff = now - timedelta(days=ttl_days)
    search_root = root / "sessions"
    if not search_root.exists():
        return []

    candidates: list[CleanupCandidate] = []
    for path in search_root.glob(f"*/{subfolder}/*"):
        if not path.is_file():
            continue
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if modified_at > cutoff:
            continue
        candidates.append(
            CleanupCandidate(
                path=path,
                content_type=content_type,
                age_days=_age_days_from_mtime(path, now),
            )
        )
    return candidates


def _mark_file_content_purged(db_session, path: str, purged_at: datetime, reason: str) -> int:
    rows = db_session.query(DbFile).filter(DbFile.stored_path == path).all()
    for row in rows:
        row.content_purged = True
        row.content_purged_at = purged_at.replace(tzinfo=None)
        row.purge_reason = reason
    return len(rows)


def _mark_sourcemap_content_purged(db_session, path: str, purged_at: datetime, reason: str) -> int:
    rows = db_session.query(DbSourceMap).filter(DbSourceMap.stored_path == path).all()
    for row in rows:
        row.content_purged = True
        row.content_purged_at = purged_at.replace(tzinfo=None)
        row.purge_reason = reason
    return len(rows)


def _mark_candidate_purged(db_session, candidate: CleanupCandidate, purged_at: datetime) -> int:
    reason = "retention_ttl_expired"
    path = str(candidate.path)
    if candidate.content_type == "file_content":
        return _mark_file_content_purged(db_session, path, purged_at, reason)
    return _mark_sourcemap_content_purged(db_session, path, purged_at, reason)


def run_retention_cleanup(
    *,
    base_path: str | None = None,
    file_ttl_days: int | None = None,
    sourcemap_ttl_days: int | None = None,
    max_deletions: int | None = None,
    dry_run: bool = True,
    now: datetime | None = None,
) -> dict:
    """Delete expired JS/map content files from storage based on TTL settings."""
    utc_now = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
    root = _resolve_base_path(base_path)
    resolved_file_ttl = _resolve_ttl_days(file_ttl_days, settings.file_content_ttl_days)
    resolved_map_ttl = _resolve_ttl_days(sourcemap_ttl_days, settings.sourcemap_content_ttl_days)
    resolved_max_deletions = _resolve_max_deletions(max_deletions, settings.cleanup_max_deletions_per_run)

    file_candidates = list(_iter_expired_files(root, "files", resolved_file_ttl, "file_content", utc_now))
    map_candidates = list(_iter_expired_files(root, "maps", resolved_map_ttl, "sourcemap_content", utc_now))
    all_candidates = sorted(
        file_candidates + map_candidates,
        key=lambda candidate: candidate.path.stat().st_mtime,
    )
    selected_for_deletion = all_candidates[:resolved_max_deletions]
    skipped_due_to_cap = max(len(all_candidates) - len(selected_for_deletion), 0)

    deleted_paths: list[str] = []
    failed: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    events: list[dict[str, object]] = []
    purge_markers_updated = 0
    purge_markers_skipped = False

    start_event = {
        "event": "cleanup_start",
        "timestamp": utc_now.isoformat(),
        "dryRun": dry_run,
        "ttlDays": {
            "fileContent": resolved_file_ttl,
            "sourceMapContent": resolved_map_ttl,
        },
        "maxDeletions": resolved_max_deletions,
        "candidateCount": len(all_candidates),
        "selectedCount": len(selected_for_deletion),
        "skippedDueToCap": skipped_due_to_cap,
    }
    events.append(start_event)
    logger.info(json.dumps(start_event))

    db_session = None
    if not dry_run and selected_for_deletion:
        try:
            db_session = SessionLocal()
        except Exception as exc:  # pragma: no cover
            purge_markers_skipped = True
            warning = {
                "warning": "purge_marker_session_unavailable",
                "message": str(exc),
            }
            warnings.append(warning)
            warning_event = {
                "event": "cleanup_warning",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "code": "purge_marker_session_unavailable",
                "message": str(exc),
            }
            events.append(warning_event)
            logger.warning(json.dumps(warning_event))

    if not dry_run:
        for candidate in selected_for_deletion:
            try:
                candidate.path.unlink(missing_ok=True)
                deleted_paths.append(str(candidate.path))
                delete_event = {
                    "event": "content_deleted",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "path": str(candidate.path),
                    "contentType": candidate.content_type,
                    "ageDays": candidate.age_days,
                }
                events.append(delete_event)
                logger.info(json.dumps(delete_event))
                if db_session is not None:
                    try:
                        updated_rows = _mark_candidate_purged(
                            db_session=db_session,
                            candidate=candidate,
                            purged_at=datetime.now(timezone.utc),
                        )
                        db_session.commit()
                        purge_markers_updated += updated_rows
                        marker_event = {
                            "event": "purge_marker_updated",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "path": str(candidate.path),
                            "rowsUpdated": updated_rows,
                            "contentType": candidate.content_type,
                        }
                        events.append(marker_event)
                        logger.info(json.dumps(marker_event))
                    except Exception as exc:  # pragma: no cover
                        db_session.rollback()
                        purge_markers_skipped = True
                        warning = {
                            "warning": "purge_marker_update_failed",
                            "path": str(candidate.path),
                            "message": str(exc),
                        }
                        warnings.append(warning)
                        warning_event = {
                            "event": "cleanup_warning",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "code": "purge_marker_update_failed",
                            "path": str(candidate.path),
                            "message": str(exc),
                        }
                        events.append(warning_event)
                        logger.warning(json.dumps(warning_event))
            except Exception as exc:  # pragma: no cover
                failed.append({"path": str(candidate.path), "error": str(exc)})
                failure_event = {
                    "event": "content_delete_failed",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "path": str(candidate.path),
                    "contentType": candidate.content_type,
                    "error": str(exc),
                }
                events.append(failure_event)
                logger.error(json.dumps(failure_event))
    if db_session is not None:
        db_session.close()

    finish_event = {
        "event": "cleanup_finished",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dryRun": dry_run,
        "candidateCount": len(all_candidates),
        "selectedCount": len(selected_for_deletion),
        "deletedCount": len(deleted_paths),
        "failedCount": len(failed),
        "skippedDueToCap": skipped_due_to_cap,
        "purgeMarkersUpdated": purge_markers_updated,
        "purgeMarkersSkipped": purge_markers_skipped,
    }
    events.append(finish_event)
    logger.info(json.dumps(finish_event))

    return {
        "success": len(failed) == 0,
        "dryRun": dry_run,
        "now": utc_now.isoformat(),
        "ttlDays": {
            "fileContent": resolved_file_ttl,
            "sourceMapContent": resolved_map_ttl,
        },
        "candidates": {
            "fileContent": [
                {"path": str(candidate.path), "ageDays": candidate.age_days}
                for candidate in file_candidates
            ],
            "sourceMapContent": [
                {"path": str(candidate.path), "ageDays": candidate.age_days}
                for candidate in map_candidates
            ],
        },
        "summary": {
            "candidates": len(all_candidates),
            "selectedForDeletion": len(selected_for_deletion),
            "deleted": len(deleted_paths),
            "failed": len(failed),
            "skippedDueToCap": skipped_due_to_cap,
            "capped": skipped_due_to_cap > 0,
            "purgeMarkersUpdated": purge_markers_updated,
            "purgeMarkersSkipped": purge_markers_skipped,
        },
        "guardrails": {
            "maxDeletionsPerRun": resolved_max_deletions,
        },
        "deletedPaths": deleted_paths,
        "failures": failed,
        "warnings": warnings,
        "events": events,
    }
