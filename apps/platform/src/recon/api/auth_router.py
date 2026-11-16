"""Authentication routes — the central login (username + password -> session token).

``POST /auth/login`` is the ONLY route that reads credentials and the ONLY one that
resolves a tenant WITHOUT a prior tenant context (it looks the user up cross-tenant
via the admin connection — see recon.auth.service). Everything else derives its
tenant from the signed token this endpoint mints (api/deps.get_tenant_id).

When auth is disabled (empty ``RECON_AUTH_SECRET``) login returns 503 (a soft-fail) —
the app still runs on the X-Tenant-Id header stand-in.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel
from redis import Redis

from recon.api.deps import _bearer_claims, get_login_redis, get_principal
from recon.auth import handoff, login_rate_limit
from recon.auth import service as auth_service
from recon.auth import token as auth_token
from recon.auth.service import Principal, authenticate, tenant_name
from recon.config import get_settings
from recon.observability import get_logger

log = get_logger("recon.api.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class TenantInfo(BaseModel):
    id: str
    name: str | None


class LoginResponse(BaseModel):
    token: str
    user: str
    role: str
    tenant: TenantInfo


class MeResponse(BaseModel):
    user_id: str
    role: str
    tenant: TenantInfo


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, redis: Redis = Depends(get_login_redis)) -> LoginResponse:
    settings = get_settings()
    if not settings.auth_secret:
        # Auth not configured — soft-fail (503), so a header-mode dev
        # deployment gives a clear signal instead of minting a dead credential.
        raise HTTPException(status_code=503, detail="authentication is not configured")
    # Brute-force throttle BEFORE the bcrypt verify (design review N1): a login flood
    # can't burn CPU, and repeated guesses against one account are capped. Read-only
    # here — the counter only advances on the FAILED attempt below — and fails OPEN on a
    # Redis error, so a limiter blip never locks operators out (see login_rate_limit).
    retry_after = login_rate_limit.retry_after_seconds(redis, body.username, settings=settings)
    if retry_after > 0:
        raise HTTPException(
            status_code=429,
            detail="too many login attempts; try again later",
            headers={"Retry-After": str(int(retry_after) or 1)},
        )
    principal = authenticate(body.username, body.password)
    if principal is None:
        # One generic 401 for unknown-user, bad-password, AND ambiguous-username —
        # never reveal which (no user enumeration). Count the failure toward the throttle.
        login_rate_limit.record_failure(redis, body.username, settings=settings)
        raise HTTPException(status_code=401, detail="invalid credentials")
    login_rate_limit.clear(redis, body.username, settings=settings)
    token = auth_token.mint(
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
        role=principal.role,
        key=settings.auth_secret,
        ttl_seconds=settings.auth_token_ttl_seconds,
    )
    return LoginResponse(
        token=token,
        user=body.username,
        role=principal.role,
        tenant=TenantInfo(id=principal.tenant_id, name=tenant_name(principal.tenant_id)),
    )


@router.get("/me", response_model=MeResponse)
def me(principal: Principal = Depends(get_principal)) -> MeResponse:
    """Re-validate the caller's token and echo the identity (used by the SPA to
    confirm a stored token is still good on load)."""
    return MeResponse(
        user_id=principal.user_id,
        role=principal.role,
        tenant=TenantInfo(id=principal.tenant_id, name=tenant_name(principal.tenant_id)),
    )


@router.post("/logout")
def logout() -> dict:
    """Stateless: the client discards its token. There is no server-side session to
    clear; rotating ``RECON_AUTH_SECRET`` is the platform-wide "revoke all"."""
    return {"ok": True}


class HandoffCodeResponse(BaseModel):
    code: str
    expires_in: int


class HandoffExchangeRequest(BaseModel):
    code: str


def _require_auth_configured() -> str:
    secret = get_settings().auth_secret
    if not secret:
        raise HTTPException(status_code=503, detail="authentication is not configured")
    return secret


@router.post("/handoff", response_model=HandoffCodeResponse)
def issue_handoff_code(
    response: Response,
    authorization: str | None = Header(default=None),
    redis: Redis = Depends(get_login_redis),
) -> HandoffCodeResponse:
    """ADR-0011: a ~30 s single-use code, bound to the caller's user and tenant, that the
    workspace exchanges once for a login. Needs a Bearer login token (never the tenant
    header); the code is only ever in this response body."""
    secret = _require_auth_configured()
    claims = _bearer_claims(authorization, get_settings())
    if claims is None:
        raise HTTPException(status_code=401, detail="a valid login is required")
    response.headers["Cache-Control"] = "no-store"
    try:
        code = handoff.issue_code(
            redis,
            user_id=claims.user_id,
            tenant_id=claims.tenant_id,
            session_exp=claims.exp,
            key=secret,
        )
    except handoff.HandoffRateLimited as exc:
        raise HTTPException(
            status_code=429,
            detail="too many handoff codes; try again shortly",
            headers={"Retry-After": str(handoff.ISSUE_WINDOW_SECONDS)},
        ) from exc
    except handoff.HandoffStoreError as exc:
        log.warning("auth.handoff_store_unavailable", op="issue")
        raise HTTPException(status_code=503, detail="handoff is unavailable") from exc
    log.info("auth.handoff_issued", user_id=claims.user_id, tenant_id=claims.tenant_id)
    return HandoffCodeResponse(code=code, expires_in=handoff.CODE_TTL_SECONDS)


@router.post("/handoff/exchange", response_model=LoginResponse)
def exchange_handoff_code(
    body: HandoffExchangeRequest,
    response: Response,
    redis: Redis = Depends(get_login_redis),
) -> LoginResponse:
    """ADR-0011: spend a handoff code for a normal login. Every failure is the same 401."""
    settings = get_settings()
    secret = _require_auth_configured()
    if not handoff.is_well_formed(body.code):
        # No echo of the submitted value (the default 422 body would repeat it).
        raise HTTPException(status_code=422, detail="invalid code")
    response.headers["Cache-Control"] = "no-store"
    try:
        grant = handoff.consume_code(redis, body.code, key=secret)
    except handoff.HandoffStoreError as exc:
        log.warning("auth.handoff_store_unavailable", op="exchange")
        raise HTTPException(status_code=503, detail="handoff is unavailable") from exc
    user = auth_service.lookup_user(grant.user_id) if grant is not None else None
    # Re-read from the database: the user must still exist in the bound tenant; the role
    # is today's, not the issuing token's.
    if grant is None or user is None or user.tenant_id != grant.tenant_id:
        log.info("auth.handoff_rejected")
        raise HTTPException(status_code=401, detail="invalid or expired code")
    token = auth_token.mint(
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        role=user.role,
        key=secret,
        ttl_seconds=settings.auth_token_ttl_seconds,
        # A handoff moves a session; it never lengthens one (ADR-0011).
        expires_at=min(int(time.time()) + settings.auth_token_ttl_seconds, grant.session_exp),
    )
    log.info("auth.handoff_exchanged", user_id=user.user_id, tenant_id=user.tenant_id)
    return LoginResponse(
        token=token,
        user=user.username,
        role=user.role,
        tenant=TenantInfo(id=user.tenant_id, name=auth_service.tenant_name(user.tenant_id)),
    )
