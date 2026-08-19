"""Taught HTTP-client wrappers for a run's session (REQ-C2 first clause, spec §6).

POST/GET/DELETE /runs/{run_id}/wrappers. Thin: validate the callee, delegate to
recon.findings.wrapper_service (which persists + re-extracts), map RLS-invisible
runs to 404, a non-identifier callee to 422, and a vanished source blob to 409.
Isolation is the database's (RLS).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from recon.api.deps import get_tenant_id
from recon.findings import reextract, wrapper_service
from recon.findings.wrappers import InvalidWrapperCallee

router = APIRouter(tags=["wrappers"])


class WrapperRuleIn(BaseModel):
    callee: str
    actor: str | None = None


@router.post("/runs/{run_id}/wrappers")
async def add_wrapper_rule(
    run_id: str,
    rule: WrapperRuleIn,
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    try:
        result = await run_in_threadpool(
            wrapper_service.add_rule,
            tenant_id,
            run_id,
            callee=rule.callee,
            actor=rule.actor,
        )
    except InvalidWrapperCallee as exc:
        raise HTTPException(status_code=422, detail=f"invalid callee: {exc}") from exc
    except reextract.SourceBlobMissing as exc:
        raise HTTPException(status_code=409, detail="run source is no longer available") from exc
    except reextract.StaleFindingIdentity as exc:
        raise HTTPException(
            status_code=409,
            detail="this run's findings predate the current finding-identity version; "
            "re-run the target to teach wrappers under it",
        ) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="run not found")
    return result


@router.get("/runs/{run_id}/wrappers")
async def list_wrapper_rules(
    run_id: str,
    tenant_id: str = Depends(get_tenant_id),
) -> list[dict]:
    rules = await run_in_threadpool(wrapper_service.list_rules, tenant_id, run_id)
    if rules is None:
        raise HTTPException(status_code=404, detail="run not found")
    return rules


@router.delete("/runs/{run_id}/wrappers/{rule_id}", status_code=204)
async def delete_wrapper_rule(
    run_id: str,
    rule_id: str,
    tenant_id: str = Depends(get_tenant_id),
) -> Response:
    deleted = await run_in_threadpool(wrapper_service.delete_rule, tenant_id, run_id, rule_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail="run not found")
    if not deleted:
        raise HTTPException(status_code=404, detail="rule not found")
    return Response(status_code=204)
