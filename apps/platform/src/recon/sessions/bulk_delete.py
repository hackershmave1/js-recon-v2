"""Delete a batch of sessions in one request (the Sessions page's multi-select).

Each id goes through the SAME single-session delete (``service.delete_session``: its own
transaction, cascade, audit log line and blob purge), so a bulk delete can never behave
differently from deleting one session at a time. Never all-or-nothing: one failure doesn't stop
the rest, and the caller gets back what was deleted, what didn't exist (or isn't this tenant's —
RLS makes the two indistinguishable, as for the single delete) and what failed.

The batch is bounded (``MAX_BATCH``): every delete purges its runs' blobs synchronously, so an
unbounded request could outlive a proxy timeout and lose the very report it promises. The UI
sends a larger selection as several batches.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from recon.observability import get_logger
from recon.sessions import service

MAX_BATCH = 50

_logger = get_logger(__name__)


@dataclass
class BulkDeleteResult:
    deleted: list[str] = field(default_factory=list)
    not_found: list[str] = field(default_factory=list)
    # Error CLASS only: a raw message can carry SQL or object-store internals.
    failed: list[dict[str, str]] = field(default_factory=list)


def delete_sessions(tenant_id: str, session_ids: list[str], actor: str | None) -> BulkDeleteResult:
    result = BulkDeleteResult()
    for session_id in session_ids:
        try:
            found = service.delete_session(tenant_id, session_id, actor=actor)
        except Exception as exc:
            _logger.error(
                "session.bulk_delete_item_failed",
                tenant_id=tenant_id,
                session_id=session_id,
                exc_info=True,
            )
            result.failed.append({"id": session_id, "error": type(exc).__name__})
            continue
        (result.deleted if found else result.not_found).append(session_id)
    _logger.info(
        "session.bulk_delete",
        actor=actor,
        tenant_id=tenant_id,
        requested=len(session_ids),
        deleted=len(result.deleted),
        not_found=len(result.not_found),
        failed=len(result.failed),
    )
    return result
