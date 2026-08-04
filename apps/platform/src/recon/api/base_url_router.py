"""Manual base-URL rules for a run's session (REQ-C2, spec §6).

POST/GET/DELETE /runs/{run_id}/base-url. Thin: validate the body, delegate to
recon.spec.base_url_service (which persists + reclassifies), map RLS-invisible
runs to 404 and an invalid base/kind to 422. Isolation is the database's (RLS).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from recon.api.deps import get_tenant_id
from recon.findings.base_url import InvalidBaseUrl
from recon.spec import base_url_service

router = APIRouter(tags=["base-url"])


class BaseUrlRuleIn(BaseModel):
    kind: Literal["prefix", "selection"]
    base_url: str
    path_prefix: str | None = None
    finding_hashes: list[str] | None = None
    actor: str | None = None


def _validate_shape(rule: BaseUrlRuleIn) -> None:
    if rule.kind == "prefix" and (not rule.path_prefix or rule.finding_hashes):
        raise HTTPException(status_code=422, detail="a prefix rule needs path_prefix and no finding_hashes")
    if rule.kind == "selection" and (not rule.finding_hashes or rule.path_prefix):
        raise HTTPException(status_code=422, detail="a selection rule needs finding_hashes and no path_prefix")


@router.post("/runs/{run_id}/base-url")
async def add_base_url_rule(
    run_id: str, rule: BaseUrlRuleIn, tenant_id: str = Depends(get_tenant_id),
) -> dict:
    _validate_shape(rule)
    try:
        result = await run_in_threadpool(
            base_url_service.add_rule, tenant_id, run_id,
            kind=rule.kind, base_url=rule.base_url, path_prefix=rule.path_prefix,
            finding_hashes=rule.finding_hashes, actor=rule.actor,
        )
    except InvalidBaseUrl as exc:
        raise HTTPException(status_code=422, detail=f"invalid base_url: {exc}") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="run not found")
    return result


@router.get("/runs/{run_id}/base-url")
async def list_base_url_rules(
    run_id: str, tenant_id: str = Depends(get_tenant_id),
) -> list[dict]:
    rules = await run_in_threadpool(base_url_service.list_rules, tenant_id, run_id)
    if rules is None:
        raise HTTPException(status_code=404, detail="run not found")
    return rules


@router.delete("/runs/{run_id}/base-url/{rule_id}", status_code=204)
async def delete_base_url_rule(
    run_id: str, rule_id: str, tenant_id: str = Depends(get_tenant_id),
) -> Response:
    deleted = await run_in_threadpool(base_url_service.delete_rule, tenant_id, run_id, rule_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail="run not found")
    if not deleted:
        raise HTTPException(status_code=404, detail="rule not found")
    return Response(status_code=204)
