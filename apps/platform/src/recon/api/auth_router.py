"""Authentication routes — the central login (username + password -> session token).

``POST /auth/login`` is the ONLY route that reads credentials and the ONLY one that
resolves a tenant WITHOUT a prior tenant context (it looks the user up cross-tenant
via the admin connection — see recon.auth.service). Everything else derives its
tenant from the signed token this endpoint mints (api/deps.get_tenant_id).

When auth is disabled (empty ``RECON_AUTH_SECRET``) login returns 503, mirroring the
pairing endpoint's soft-fail — the app still runs on the X-Tenant-Id header stand-in.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from recon.api.deps import get_principal
from recon.auth import token as auth_token
from recon.auth.service import Principal, authenticate, tenant_name
from recon.config import get_settings

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
def login(body: LoginRequest) -> LoginResponse:
    settings = get_settings()
    if not settings.auth_secret:
        # Auth not configured — soft-fail like pairing mint, so a header-mode dev
        # deployment gives a clear signal instead of minting a dead credential.
        raise HTTPException(status_code=503, detail="authentication is not configured")
    principal = authenticate(body.username, body.password)
    if principal is None:
        # One generic 401 for unknown-user, bad-password, AND ambiguous-username —
        # never reveal which (no user enumeration).
        raise HTTPException(status_code=401, detail="invalid credentials")
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
