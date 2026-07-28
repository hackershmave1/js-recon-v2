"""Spec attach + classify endpoint (design §6.3): ``POST /runs/{run_id}/spec``
attaches an analyst-supplied OpenAPI 3.x / Swagger 2.0 spec to a run's session
and classifies its endpoint findings against it.

Thin: the raw spec bytes (JSON or YAML text, no multipart) are read straight
off the request body and handed to ``recon.spec.service.attach_and_classify``;
storage, ingestion (hardened parse + validation), and classification all live
in ``recon.spec``. Isolation is the database's (RLS), same as the other
routers -- no extra auth logic here.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request

from recon.api.deps import get_tenant_id
from recon.spec import service
from recon.spec.ingest import SpecError

router = APIRouter(tags=["spec"])


@router.post("/runs/{run_id}/spec")
async def attach_spec(
    run_id: str,
    request: Request,
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    # Raw body, not `bytes = Body(...)`: a spec is JSON or YAML text pasted or
    # uploaded whole, never multipart, and `request.body()` reads it regardless
    # of whatever (or no) Content-Type the caller sent -- the same "read the
    # untrusted bytes, let the service validate them" shape `attach_and_classify`
    # already assumes (its own docstring: a spec sits on the same footing as
    # target JS).
    raw_spec = await request.body()
    try:
        summary = service.attach_and_classify(tenant_id, run_id, raw_spec)
    except SpecError as exc:
        raise HTTPException(status_code=422, detail=f"invalid spec: {exc}") from exc
    if summary is None:
        raise HTTPException(status_code=404, detail="run not found")
    return asdict(summary)
