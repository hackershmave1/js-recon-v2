"""Discovered-hosts read endpoint: ``GET /runs/{run_id}/hosts`` (DEBT D26).

A thin read over the per-run host inventory recon surfaced (assets · endpoint hosts ·
suspected-backend hosts · tech · declared base-URLs), each classified in/out of the
session's declared scope by the canonical egress guard. Isolation is the database's (RLS):
a run absent for this tenant is a 404, distinct from a real run with no hosts
(200 + empty ``hosts``)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from recon.api.deps import get_tenant_id
from recon.findings import hosts

router = APIRouter(tags=["hosts"])


@router.get("/runs/{run_id}/hosts")
def get_run_hosts(
    run_id: str,
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    result = hosts.list_hosts(tenant_id, run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="run not found")
    return {
        "run_id": result.run_id,
        "count": result.count,
        "in_scope": result.in_scope,
        "endpoints_unattributed": result.endpoints_unattributed,
        "suspected_unattributed": result.suspected_unattributed,
        "hosts": [
            {
                "host": row.host,
                "in_scope": row.in_scope,
                "declared": row.declared,
                "assets": row.assets,
                "endpoints": row.endpoints,
                "suspected": row.suspected,
                "routes": row.routes,
                "techs": row.techs,
            }
            for row in result.hosts
        ],
    }
