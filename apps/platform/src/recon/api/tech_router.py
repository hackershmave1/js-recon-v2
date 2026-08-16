"""Technologies read endpoint: ``GET /runs/{run_id}/technologies`` (tech detection).

A thin read over the per-host technology stack the analyze fingerprint pass produced.
Isolation is the database's (RLS): a run absent for this tenant is a 404, distinct
from a run with zero detected technologies (200 + empty ``hosts``)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from recon.api.deps import get_tenant_id
from recon.findings import queries

router = APIRouter(tags=["technologies"])


@router.get("/runs/{run_id}/technologies")
def get_run_technologies(
    run_id: str,
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    result = queries.list_technologies(tenant_id, run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="run not found")
    return {
        "run_id": result.run_id,
        "count": sum(len(techs) for techs in result.hosts.values()),
        "hosts": {
            host: [
                {
                    "name": tech.name,
                    "categories": tech.categories,
                    "version": tech.version,
                    "confidence": tech.confidence,
                    "evidence": tech.evidence,
                }
                for tech in techs
            ]
            for host, techs in result.hosts.items()
        },
    }
