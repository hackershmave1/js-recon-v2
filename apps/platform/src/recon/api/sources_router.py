"""Serve a run's stored JavaScript source for the UI code viewer (R5).

``GET /runs/{run_id}/sources``               — the run's source files (list).
``GET /runs/{run_id}/sources/content?path=`` — one file's bytes decoded to text.

Thin: enumeration + the blob read live in ``recon.probe.sources`` (RLS-scoped,
mirroring ``reveal.py``). See that module's note on why source viewing is
intentionally not an audited secret-reveal action. Blocking DB + boto3 reads run
off the event loop, like ``export_router``."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool

from recon.api.deps import get_tenant_id
from recon.observability import get_logger
from recon.probe import source_search, sources

router = APIRouter(tags=["sources"])

_log = get_logger(__name__)


@router.get("/runs/{run_id}/sources")
async def get_run_sources(run_id: str, tenant_id: str = Depends(get_tenant_id)) -> dict:
    started = time.perf_counter()
    files = await run_in_threadpool(sources.list_sources, tenant_id, run_id)
    if files is None:
        raise HTTPException(status_code=404, detail="run not found")
    # Observability (D25): the Sources endpoint served large trees with no trace, so
    # a pathological run (hundreds-to-thousands of files) was invisible in logs. Log
    # the tree size + query time; the SPA's long-task observer covers the client-side
    # render cost that this size drives.
    _log.info(
        "sources.list",
        run_id=run_id,
        source_count=len(files),
        elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
    )
    return {
        "run_id": run_id,
        "count": len(files),
        "sources": [
            {
                "path": f.path,
                "kind": f.kind,
                "fetch_status": f.fetch_status,
                "asset_url": f.asset_url,
            }
            for f in files
        ],
    }


@router.get("/runs/{run_id}/sources/search")
async def search_run_sources(
    run_id: str,
    q: str,
    client: str = "",
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    """D52: run-scoped full-text search across the run's sources (recon.probe.source_search)."""
    started = time.perf_counter()
    # `client` (the Sources page's own id) scopes cancellation: only that page's older query stops.
    outcome = await run_in_threadpool(
        lambda: source_search.search(tenant_id, run_id, q, client=client[:64])
    )
    if outcome is None:
        raise HTTPException(status_code=404, detail="run not found")
    _log.info(
        "sources.search",
        run_id=run_id,
        query_len=len(q or ""),
        matches=len(outcome.matches),
        total=outcome.total,
        files_scanned=outcome.files_scanned,
        files_total=outcome.files_total,
        unindexed=outcome.unindexed,
        partial=outcome.partial,
        elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
    )
    return {
        "run_id": run_id,
        "count": len(outcome.matches),
        # Every hit, even past the display caps; files_scanned < files_total only when partial
        # (files_total counts searchable files: a failed-fetch asset has no bytes to search)
        # (the time budget ran out, or a newer search for this run superseded this one).
        "total": outcome.total,
        "files_scanned": outcome.files_scanned,
        "files_total": outcome.files_total,
        "unindexed": outcome.unindexed,
        "partial": outcome.partial,
        "matches": [
            {
                "path": m.path,
                "kind": m.kind,
                "asset_url": m.asset_url,
                "line": m.line,
                "col": m.col,
                "exact": m.exact,
                "snippet": m.snippet,
            }
            for m in outcome.matches
        ],
    }


@router.get("/runs/{run_id}/sources/content")
async def get_run_source_content(
    run_id: str,
    path: str,
    tenant_id: str = Depends(get_tenant_id),
    asset_url: str | None = None,
) -> dict:
    content = await run_in_threadpool(
        sources.get_source_content, tenant_id, run_id, path, asset_url
    )
    if content is None:
        raise HTTPException(status_code=404, detail="source not found")
    return {
        "path": content.path,
        "content": content.content,
        "truncated": content.truncated,
        "formatted": content.formatted,
    }
