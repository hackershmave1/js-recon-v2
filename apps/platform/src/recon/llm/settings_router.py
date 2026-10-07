"""Team-wide LLM settings: GET/PUT/DELETE /settings/llm + POST /settings/llm/test.

Thin: validate, delegate to llm.tenant_config, map results to HTTP. Reads are open to
the whole team; writes need an admin (token gate here, current DB role in the service)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from recon.api.deps import get_optional_principal, get_tenant_id, require_admin
from recon.auth.service import Principal
from recon.llm import service as llm_service
from recon.llm import tenant_config
from recon.llm.crypto import KeyDecryptError
from recon.llm.provider import DEFAULT_MODELS, VALID_PROVIDERS
from recon.observability import get_logger

router = APIRouter(tags=["settings"])
log = get_logger("recon.llm.team_config")

_NOT_ADMIN = "admin role required"


class TeamLlmConfigIn(BaseModel):
    provider: str
    model: str
    api_key: str = ""  # empty keeps the stored key


@router.get("/settings/llm")
async def get_team_llm_settings(
    tenant_id: str = Depends(get_tenant_id),
    principal: Principal | None = Depends(get_optional_principal),
) -> dict:
    # configured_by (an email) only with a real login, so a header-only read
    # (RECON_ALLOW_HEADER_TENANT) never exposes an admin's address.
    config = await run_in_threadpool(
        tenant_config.get_config, tenant_id, include_actor=principal is not None
    )
    return {
        "config": config,
        "can_edit": principal is not None and principal.role == "admin",
        "default_models": DEFAULT_MODELS,
        "providers": sorted(VALID_PROVIDERS),
    }


@router.put("/settings/llm")
async def save_team_llm_settings(
    body: TeamLlmConfigIn, principal: Principal = Depends(require_admin)
) -> dict:
    if body.provider not in VALID_PROVIDERS:
        raise HTTPException(
            status_code=422, detail=f"provider must be one of {sorted(VALID_PROVIDERS)}"
        )
    if not body.model.strip():
        raise HTTPException(status_code=422, detail="model must not be empty")
    try:
        result = await run_in_threadpool(
            tenant_config.save_config,
            principal.tenant_id,
            principal.user_id,
            body.provider,
            body.model.strip(),
            body.api_key.strip(),
        )
    except tenant_config.ProviderKeyRequired as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=403, detail=_NOT_ADMIN)
    return result


@router.delete("/settings/llm", status_code=204)
async def delete_team_llm_settings(principal: Principal = Depends(require_admin)) -> Response:
    deleted = await run_in_threadpool(
        tenant_config.delete_config, principal.tenant_id, principal.user_id
    )
    if deleted is None:
        raise HTTPException(status_code=403, detail=_NOT_ADMIN)
    if not deleted:
        raise HTTPException(status_code=404, detail="no team LLM config")
    return Response(status_code=204)


@router.post("/settings/llm/test")
async def test_team_llm_settings(principal: Principal = Depends(require_admin)) -> dict:
    if not await run_in_threadpool(tenant_config.is_admin, principal.tenant_id, principal.user_id):
        raise HTTPException(status_code=403, detail=_NOT_ADMIN)
    try:
        credentials = await run_in_threadpool(tenant_config.load_key, principal.tenant_id)
    except KeyDecryptError as exc:
        raise HTTPException(
            status_code=400, detail="stored key could not be decrypted; re-save it"
        ) from exc
    if credentials is None:
        raise HTTPException(status_code=400, detail="no team key saved")
    provider_name, model, api_key = credentials
    # Awaited on the request loop (see llm.service.ping_credentials).
    error = await llm_service.ping_credentials(provider_name, model, api_key)
    log.info(
        "llm.team_config.tested",
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        provider=provider_name,
        model=model,
        ok=error is None,
    )
    if error is not None:
        raise HTTPException(status_code=400, detail=error)
    await run_in_threadpool(tenant_config.mark_tested, principal.tenant_id)
    return {"ok": True, "provider": provider_name, "model": model}
