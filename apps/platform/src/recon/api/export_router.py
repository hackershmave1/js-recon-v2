"""Export a run's reconstructed API as an OpenAPI 3.0.3 document (spec §6).

GET /runs/{run_id}/export/openapi?format=json|yaml — the inverse of the spec
attach/classify endpoint. Thin: reconstruct the run's requests (RLS-scoped),
serialize + self-validate in recon.probe.openapi, and stream the bytes as a file
download. No persistence; the threat-model stage calls build_openapi in-process.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from recon.api.deps import get_tenant_id
from recon.findings import queries
from recon.probe import openapi
from recon.probe.reconstruct import reconstruct_run

router = APIRouter(tags=["export"])


def _render(requests, graphql_operations, run_id: str, fmt: str) -> tuple[bytes, str]:
    document = openapi.build_openapi(requests, run_id=run_id, graphql_operations=graphql_operations)
    return openapi.dump_openapi(document, fmt)


@router.get("/runs/{run_id}/export/openapi")
async def export_openapi(
    run_id: str,
    format: str = "json",
    tenant_id: str = Depends(get_tenant_id),
) -> Response:
    if format not in ("json", "yaml"):
        raise HTTPException(status_code=422, detail="format must be 'json' or 'yaml'")
    # reconstruct_run is a blocking DB read; keep it off the event loop like spec_router.
    requests = await run_in_threadpool(reconstruct_run, tenant_id, run_id)
    if requests is None:
        raise HTTPException(status_code=404, detail="run not found")
    # GraphQL operations (enrichment C) are a separate run-level artifact, unioned across
    # the run's assets — export-only, never part of the reconstructed HTTP paths.
    graphql_operations = await run_in_threadpool(queries.graphql_operations, tenant_id, run_id)
    try:
        body, media_type = await run_in_threadpool(
            _render, requests, graphql_operations, run_id, format
        )
    except Exception as exc:  # noqa: BLE001 — self-validation backstop (gate B2) → 500
        raise HTTPException(
            status_code=500, detail="failed to build a valid OpenAPI document"
        ) from exc
    filename = f"openapi-{run_id}.{format}"
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
