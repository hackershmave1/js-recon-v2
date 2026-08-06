"""Serve a run's stored JavaScript source for the UI code viewer (R5).

``GET /runs/{run_id}/sources``               — the run's source files (list).
``GET /runs/{run_id}/sources/content?path=`` — one file's bytes decoded to text.

Thin: enumeration + the blob read live in ``recon.probe.sources`` (RLS-scoped,
mirroring ``reveal.py``). See that module's note on why source viewing is
intentionally not an audited secret-reveal action. Blocking DB + boto3 reads run
off the event loop, like ``export_router``."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool

from recon.api.deps import get_tenant_id
from recon.probe import sources

router = APIRouter(tags=["sources"])


@router.get("/runs/{run_id}/sources")
async def get_run_sources(run_id: str, tenant_id: str = Depends(get_tenant_id)) -> dict:
    files = await run_in_threadpool(sources.list_sources, tenant_id, run_id)
    if files is None:
        raise HTTPException(status_code=404, detail="run not found")
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
    return {"path": content.path, "content": content.content, "truncated": content.truncated}
