"""LLM config routes — GET/POST/DELETE /sessions/{id}/llm-config + /test.

Thin: validate, delegate to llm.service, map None -> 404.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from recon.api.deps import get_tenant_id
from recon.llm import service
from recon.llm.provider import VALID_PROVIDERS

router = APIRouter(tags=["llm-config"])


class LlmConfigIn(BaseModel):
    provider: str
    model: str
    api_key: str = ""  # omit or leave empty to keep the existing stored key


@router.post("/sessions/{session_id}/llm-config", status_code=201)
async def save_llm_config(
    session_id: str,
    body: LlmConfigIn,
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    if body.provider not in VALID_PROVIDERS:
        raise HTTPException(
            status_code=422,
            detail=f"provider must be one of {sorted(VALID_PROVIDERS)}",
        )
    if not body.model.strip():
        raise HTTPException(status_code=422, detail="model must not be empty")

    result = await run_in_threadpool(
        service.save_config,
        tenant_id,
        session_id,
        body.provider,
        body.model.strip(),
        body.api_key.strip(),
    )
    if result is None:
        raise HTTPException(status_code=404, detail="session not found")
    return result


@router.get("/sessions/{session_id}/llm-config")
async def get_llm_config(
    session_id: str,
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    result = await run_in_threadpool(service.get_config, tenant_id, session_id)
    if result is None:
        raise HTTPException(status_code=404, detail="no LLM config for this session")
    return result


@router.delete("/sessions/{session_id}/llm-config", status_code=204)
async def delete_llm_config(
    session_id: str,
    tenant_id: str = Depends(get_tenant_id),
) -> Response:
    deleted = await run_in_threadpool(service.delete_config, tenant_id, session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="no LLM config for this session")
    return Response(status_code=204)


@router.post("/sessions/{session_id}/llm-config/test")
async def test_llm_config(
    session_id: str,
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    result = await run_in_threadpool(service.test_config, tenant_id, session_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "test failed"))
    return result
