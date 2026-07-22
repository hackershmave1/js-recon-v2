"""Manual-probe handoff endpoints (REQ-P1).

``GET /runs/{run_id}/requests`` returns each reconstructed request with inline
curl + raw-HTTP artifacts. ``POST /runs/{run_id}/findings/{finding_hash}/triage``
records a mark-confirmed / triage verdict. Both are thin: reconstruct/serialize
and the triage upsert live in ``recon.probe``. Isolation is the database's (RLS)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from recon.api.deps import get_tenant_id
from recon.probe import reconstruct, serialize, triage
from recon.probe.reconstruct import ReconstructedRequest

router = APIRouter(tags=["probe"])


class TriageRequest(BaseModel):
    status: str
    note: str | None = None
    actor: str | None = None


@router.get("/runs/{run_id}/requests")
def get_run_requests(run_id: str, tenant_id: str = Depends(get_tenant_id)) -> dict:
    requests = reconstruct.reconstruct_run(tenant_id, run_id)
    if requests is None:
        raise HTTPException(status_code=404, detail="run not found")
    return {
        "run_id": run_id,
        "count": len(requests),
        "requests": [_request_dict(request) for request in requests],
    }


@router.post("/runs/{run_id}/findings/{finding_hash}/triage")
def set_finding_triage(
    run_id: str,
    finding_hash: str,
    body: TriageRequest,
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    if body.status not in triage.VALID_STATUSES:
        raise HTTPException(status_code=400, detail="invalid triage status")
    state = triage.set_triage_for_run(
        tenant_id, run_id, finding_hash,
        status=body.status, note=body.note, actor=body.actor,
    )
    if state is None:
        raise HTTPException(status_code=404, detail="run not found")
    return {
        "finding_hash": finding_hash,
        "status": state.status,
        "note": state.note,
        "actor": state.actor,
        "updated_at": state.updated_at,
    }


def _request_dict(request: ReconstructedRequest) -> dict:
    artifacts = (
        None
        if not request.probeable
        else {"curl": serialize.to_curl(request), "http": serialize.to_http(request)}
    )
    return {
        "operation": request.operation,
        "method": request.method,
        "path": request.path,
        "hosts": list(request.hosts),
        "query_params": [{"name": q.name, "example": q.example} for q in request.query_params],
        "body_params": list(request.body_params),
        "content_type": request.content_type,
        "example_url": request.example_url,
        "probeable": request.probeable,
        "endpoint_hash": request.endpoint_hash,
        "artifacts": artifacts,
    }
