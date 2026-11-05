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

import os
import tempfile
import time
import uuid
from typing import Any

from redis import Redis

from recon import storage
from recon.config import get_settings
from recon.domain import QueueName
from recon.findings import deobfuscate, sourcemapper
from recon.observability import get_logger
from recon.probe import formatted_text, recovered_store, sources
from recon.queue import streams
from recon.runs import assets as run_assets

_log = get_logger(__name__)

SLICE_SECONDS = 5.0
# The in-flight marker outlives a slow slice (each re-enqueue refreshes it) but not a dead job.
_INDEXING_TTL = 600
# "Nothing left to index" — skips the listing on later opens; losing it only costs one listing.
_INDEXED_TTL = 30 * 24 * 3600
# A job that ENDED with bundles still unindexed (a write failed, e.g. an object-store blip): don't
# re-run it on every open, but do retry within the hour rather than leave the run raw for a month.
_RETRY_AFTER_TTL = 3600


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


def _stored_lists(tenant_id: str, run_id: str) -> set[str]:
    """Digests of bundles whose recovered originals are stored (recon.probe.recovered_store)."""
    prefix = f"{tenant_id}/{run_id}/originals/"
    return {key[len(prefix) :] for key in storage.list_keys(prefix)}


def _map_refs(tenant_id: str, run_id: str) -> dict[str, str]:
    """Bundle path -> its source-map blob, for the bundles that have one (under RLS)."""
    return {
        row.url: row.source_map_ref
        for row in run_assets.list_for_run(tenant_id, run_id)
        if row.fetch_status == "ok" and row.input_ref and row.source_map_ref
    }


def status(redis: Redis, tenant_id: str, run_id: str) -> dict[str, Any]:
    """``{indexed, total, indexing}`` from object-store LISTINGS (never a download). A unit is a
    bundle's formatted text, plus — for a bundle with a source map — its stored originals."""
    bundles = _bundles(tenant_id, run_id)
    stored = _stored_digests(tenant_id, run_id) if bundles else set()
    maps = _map_refs(tenant_id, run_id) if bundles else {}
    lists = _stored_lists(tenant_id, run_id) if maps else set()
    indexed = sum(1 for _path, key in bundles if key.rsplit("/", 1)[1] in stored)
    indexed += sum(1 for path, key in bundles if path in maps and key.rsplit("/", 1)[1] in lists)
    return {
        "indexed": indexed,
        "total": len(bundles) + len(maps),
        "indexing": bool(redis.exists(_indexing_key(tenant_id, run_id))),
    }


def request_index(redis: Redis, tenant_id: str, run_id: str) -> dict[str, Any]:
    """Start indexing this run if it has unindexed bundles and no job is running (at most one job
    per run: safe on every Sources open), and return its ``status`` — computed once, not twice."""
    current = status(redis, tenant_id, run_id)
    if redis.exists(_indexed_key(tenant_id, run_id)) or current["indexed"] >= current["total"]:
        return current
    token = uuid.uuid4().hex
    if not redis.set(_indexing_key(tenant_id, run_id), token, nx=True, ex=_INDEXING_TTL):
        return current
    streams.enqueue(
        redis,
        QueueName.INDEX,
        {"tenant_id": tenant_id, "run_id": run_id, "cursor": 0, "token": token},
    )
    _log.info("source_index.requested", tenant_id=tenant_id, run_id=run_id, **current)
    return {**current, "indexing": True}


def handle(redis: Redis, message: dict[str, Any]) -> str:
    """The worker's INDEX-queue handler. NEVER raises: an exception would leave the message
    un-acked and the stall reclaim would hand it back forever."""
    try:
        tenant_id = str(uuid.UUID(str(message["tenant_id"])))
        run_id = str(uuid.UUID(str(message["run_id"])))
        cursor = int(message.get("cursor", 0))
        token = str(message["token"])
    except (KeyError, ValueError, TypeError):
        _log.warning("source_index.rejected", message=message)
        return "rejected"
    # Only the chain that owns the run's job continues: a slice re-delivered by the stall reclaim
    # (its ack lost, or it outran the lease) finds a newer token and stops instead of forking a
    # second, parallel chain.
    if _owner(redis, tenant_id, run_id) != token:
        return "superseded"
    try:
        next_cursor = index_slice(tenant_id, run_id, cursor)
    except Exception:
        _log.error("source_index.failed", tenant_id=tenant_id, run_id=run_id, exc_info=True)
        redis.delete(_indexing_key(tenant_id, run_id))
        return "failed"
    if next_cursor is None:
        redis.delete(_indexing_key(tenant_id, run_id))
        # "Done" only when every bundle really has its copy: a store can fail silently (it is
        # best-effort), and a 30-day marker would then leave the run raw-searched for a month.
        final = status(redis, tenant_id, run_id)
        complete = final["indexed"] >= final["total"]
        redis.set(
            _indexed_key(tenant_id, run_id), 1, ex=_INDEXED_TTL if complete else _RETRY_AFTER_TTL
        )
        _log.info("source_index.done", tenant_id=tenant_id, run_id=run_id, **final)
        return "done" if complete else "incomplete"
    successor = uuid.uuid4().hex
    redis.set(_indexing_key(tenant_id, run_id), successor, ex=_INDEXING_TTL)
    streams.enqueue(
        redis,
        QueueName.INDEX,
        {"tenant_id": tenant_id, "run_id": run_id, "cursor": next_cursor, "token": successor},
    )
    return "continued"


def _owner(redis: Redis, tenant_id: str, run_id: str) -> str | None:
    value = redis.get(_indexing_key(tenant_id, run_id))
    return value.decode() if isinstance(value, bytes) else value


def index_slice(tenant_id: str, run_id: str, cursor: int) -> int | None:
    """Index bundles from ``cursor`` for about ``SLICE_SECONDS``; return where to resume, or None
    when the end (or a deleted run) is reached. One bundle failing never stops the rest."""
    bundles = _bundles(tenant_id, run_id)
    stored = _stored_digests(tenant_id, run_id) if bundles else set()
    maps = _map_refs(tenant_id, run_id) if bundles else {}
    lists = _stored_lists(tenant_id, run_id) if maps else set()
    deadline = time.monotonic() + SLICE_SECONDS
    for position in range(cursor, len(bundles)):
        if position > cursor and time.monotonic() > deadline:
            return position
        path, key = bundles[position]
        if path in maps and key.rsplit("/", 1)[1] not in lists:
            _index_originals(tenant_id, run_id, key, maps[path])
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
        # back into a prefix its purge already swept. (A delete landing between this check and
        # the write can still orphan one blob — the same accepted window as D72.)
        if not sources.asset_keys(tenant_id, run_id):
            return None
        formatted_text.store_served(key, text, formatted=formatted)
    return None


def _index_originals(tenant_id: str, run_id: str, bundle_key: str, map_ref: str) -> None:
    """Recover a bundle's source map ONCE (on the worker) and store its originals exactly as
    analyze does: each beautified only if minified, within the same byte budgets. Best-effort —
    a bad map or missing engine leaves the bundle's originals unstored (search falls back)."""
    settings = get_settings()
    try:
        with tempfile.TemporaryDirectory(prefix="idx-") as workdir:
            map_path = os.path.join(workdir, "bundle.map")
            with open(map_path, "wb") as handle:
                handle.write(storage.get_blob(map_ref))
            files: list[tuple[str, str]] = []
            total, partial = 0, False
            for rel, raw in sourcemapper.iter_recovered_files(
                map_path, max_recovered_bytes=settings.max_source_map_bytes
            ):
                text = deobfuscate.beautify_if_minified(raw.decode("utf-8", "replace"))
                total += len(text.encode("utf-8"))
                if total > 2 * settings.max_source_map_bytes:  # analyze's write budget
                    partial = True
                    break
                files.append((rel, text))
    except Exception:
        _log.warning("source_index.originals_failed", run_id=run_id, map_ref=map_ref, exc_info=True)
        return
    if not sources.asset_keys(tenant_id, run_id):  # deleted meanwhile: keep its prefix clean
        return
    recovered_store.store(bundle_key, files, partial=partial)
