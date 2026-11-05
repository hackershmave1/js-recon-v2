"""Background indexing of an OLDER run's sources: store each bundle's served text so search gives
its hits line numbers (recon.probe.formatted_text; runs analyzed since it existed are indexed by
analyze already).

Beautifying is pure-Python CPU (80s for a 211-bundle run), so it runs on the worker, never in the
API. One job per run (Redis ``SET NX``), carried as a chain of short INDEX-queue messages: each
handles ~5s of work from a cursor into the run's sorted bundles, then re-enqueues itself with the
next cursor. A cursor (not "what's still missing") guarantees the chain ends even when a bundle can
never be indexed; such a bundle simply stays unindexed — search still finds it, raw.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from redis import Redis

from recon import storage
from recon.domain import QueueName
from recon.findings import deobfuscate
from recon.observability import get_logger
from recon.probe import formatted_text, sources
from recon.queue import streams

_log = get_logger(__name__)

SLICE_SECONDS = 5.0
# The in-flight marker outlives a slow slice (each re-enqueue refreshes it) but not a dead job.
_INDEXING_TTL = 600
# "Nothing left to index" — skips the listing on later opens; losing it only costs one listing.
_INDEXED_TTL = 30 * 24 * 3600


def _indexing_key(tenant_id: str, run_id: str) -> str:
    return f"index:{tenant_id}:{run_id}"


def _indexed_key(tenant_id: str, run_id: str) -> str:
    return f"indexed:{tenant_id}:{run_id}"


def _bundles(tenant_id: str, run_id: str) -> list[tuple[str, str]]:
    """(path, input key) of the run's stored bundles, in a stable order for the cursor."""
    return sorted(sources.asset_keys(tenant_id, run_id).items())


def _stored_digests(tenant_id: str, run_id: str) -> set[str]:
    prefix = f"{tenant_id}/{run_id}/formatted/"
    return {key[len(prefix) :] for key in storage.list_keys(prefix)}


def status(redis: Redis, tenant_id: str, run_id: str) -> dict[str, Any]:
    """``{indexed, total, indexing}`` from ONE object-store listing (never a download)."""
    bundles = _bundles(tenant_id, run_id)
    stored = _stored_digests(tenant_id, run_id) if bundles else set()
    indexed = sum(1 for _path, key in bundles if key.rsplit("/", 1)[1] in stored)
    return {
        "indexed": indexed,
        "total": len(bundles),
        "indexing": bool(redis.exists(_indexing_key(tenant_id, run_id))),
    }


def request_index(redis: Redis, tenant_id: str, run_id: str) -> bool:
    """Start indexing this run if it has unindexed bundles and no job is running. Returns True
    when a job was enqueued. Safe to call on every Sources open: at most one job per run."""
    if redis.exists(_indexed_key(tenant_id, run_id)):
        return False
    current = status(redis, tenant_id, run_id)
    if current["indexed"] >= current["total"]:
        redis.set(_indexed_key(tenant_id, run_id), 1, ex=_INDEXED_TTL)
        return False
    if not redis.set(_indexing_key(tenant_id, run_id), 1, nx=True, ex=_INDEXING_TTL):
        return False
    streams.enqueue(redis, QueueName.INDEX, {"tenant_id": tenant_id, "run_id": run_id, "cursor": 0})
    _log.info("source_index.requested", tenant_id=tenant_id, run_id=run_id, **current)
    return True


def handle(redis: Redis, message: dict[str, Any]) -> str:
    """The worker's INDEX-queue handler. NEVER raises: an exception would leave the message
    un-acked and the stall reclaim would hand it back forever."""
    try:
        tenant_id = str(uuid.UUID(str(message["tenant_id"])))
        run_id = str(uuid.UUID(str(message["run_id"])))
        cursor = int(message.get("cursor", 0))
    except (KeyError, ValueError, TypeError):
        _log.warning("source_index.rejected", message=message)
        return "rejected"
    try:
        next_cursor = index_slice(tenant_id, run_id, cursor)
    except Exception:
        _log.error("source_index.failed", tenant_id=tenant_id, run_id=run_id, exc_info=True)
        redis.delete(_indexing_key(tenant_id, run_id))
        return "failed"
    if next_cursor is None:
        redis.delete(_indexing_key(tenant_id, run_id))
        redis.set(_indexed_key(tenant_id, run_id), 1, ex=_INDEXED_TTL)
        _log.info("source_index.done", tenant_id=tenant_id, run_id=run_id)
        return "done"
    redis.expire(_indexing_key(tenant_id, run_id), _INDEXING_TTL)
    streams.enqueue(
        redis, QueueName.INDEX, {"tenant_id": tenant_id, "run_id": run_id, "cursor": next_cursor}
    )
    return "continued"


def index_slice(tenant_id: str, run_id: str, cursor: int) -> int | None:
    """Index bundles from ``cursor`` for about ``SLICE_SECONDS``; return where to resume, or None
    when the end (or a deleted run) is reached. One bundle failing never stops the rest."""
    bundles = _bundles(tenant_id, run_id)
    stored = _stored_digests(tenant_id, run_id) if bundles else set()
    deadline = time.monotonic() + SLICE_SECONDS
    for position in range(cursor, len(bundles)):
        if position > cursor and time.monotonic() > deadline:
            return position
        path, key = bundles[position]
        if key.rsplit("/", 1)[1] in stored:
            continue
        try:
            text, formatted = deobfuscate.served_text(
                storage.get_blob(key).decode("utf-8", "replace")
            )
        except Exception:
            _log.warning("source_index.bundle_failed", run_id=run_id, path=path, exc_info=True)
            continue
        # The session may have been deleted while this beautified: writing now would put a blob
        # back into a prefix its purge already swept.
        if not sources.asset_keys(tenant_id, run_id):
            return None
        formatted_text.store(key, sources._content_from_text(path, text, formatted=formatted))
    return None
