"""Team-wide LLM settings: GET/PUT/DELETE /settings/llm + POST /settings/llm/test.

Thin: validate, delegate to llm.tenant_config, map results to HTTP. Reads are open to
the whole team; writes need an admin (token gate here, current DB role in the service)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from recon.api.deps import get_optional_principal, get_tenant_id, require_admin
from recon.auth.service import Principal
from recon.llm import catalog, cost, tenant_config
from recon.llm import service as llm_service
from recon.llm.crypto import KeyDecryptError
from recon.llm.presets import BUILTIN_PRESET_MODELS, PRESETS
from recon.llm.provider import DEFAULT_MODELS, VALID_PROVIDERS
from recon.observability import get_logger

router = APIRouter(tags=["settings"])
log = get_logger("recon.llm.team_config")

_NOT_ADMIN = "admin role required"
_MAX_MODEL_ID_LENGTH = 200
_MODEL_TOO_LONG = f"model ids must be at most {_MAX_MODEL_ID_LENGTH} characters"


class TeamLlmConfigIn(BaseModel):
    provider: str
    model: str
    api_key: str = ""  # empty keeps the stored key
    # Omitted keeps the stored overrides; null or {} clears them (see model_fields_set).
    preset_models: dict[str, str] | None = None


def _preset_views(team_provider: str | None, overrides: dict[str, str]) -> dict | None:
    if team_provider is None:
        return None
    # Cache only: this route must never block on OpenRouter (only /models fetches).
    ids = catalog.cached_ids() if team_provider == "openrouter" else None
    views = {}
    for preset in PRESETS:
        model = overrides.get(preset) or BUILTIN_PRESET_MODELS[team_provider][preset]
        views[preset] = {
            "model": model,
            "source": "team" if overrides.get(preset) else "builtin",
            # A catalog variant ("x:free") is listed as-is; a routing variant isn't.
            "available": None if ids is None else (model in ids or model.split(":")[0] in ids),
        }
    return views


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
        "presets": _preset_views(
            config["provider"] if config else None, (config or {}).get("preset_models") or {}
        ),
        "builtin_preset_models": BUILTIN_PRESET_MODELS,
    }


@router.get("/settings/llm/models")
async def list_llm_models(tenant_id: str = Depends(get_tenant_id)) -> dict:
    snapshot = await catalog.get_catalog()
    estimate = await run_in_threadpool(cost.estimate_tokens, tenant_id)
    return {**snapshot, "estimate": estimate}


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
    if len(body.model.strip()) > _MAX_MODEL_ID_LENGTH:
        raise HTTPException(status_code=422, detail=_MODEL_TOO_LONG)
    preset_models: object = tenant_config.KEEP_PRESETS
    if "preset_models" in body.model_fields_set:
        if body.preset_models is None:
            preset_models = None
        else:
            if set(body.preset_models) - set(PRESETS) or any(
                not v.strip() for v in body.preset_models.values()
            ):
                raise HTTPException(
                    status_code=422,
                    detail=f"preset_models keys must be among {list(PRESETS)} with non-empty model ids",
                )
            if any(len(v.strip()) > _MAX_MODEL_ID_LENGTH for v in body.preset_models.values()):
                raise HTTPException(status_code=422, detail=_MODEL_TOO_LONG)
            preset_models = {k: v.strip() for k, v in body.preset_models.items()}
    try:
        result = await run_in_threadpool(
            tenant_config.save_config,
            principal.tenant_id,
            principal.user_id,
            body.provider,
            body.model.strip(),
            body.api_key.strip(),
            preset_models,
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
