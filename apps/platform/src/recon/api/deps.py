"""FastAPI dependencies: the shared Redis client and tenant/identity resolution.

Tenant resolution is the platform's access boundary. Two modes, chosen by whether
``RECON_AUTH_SECRET`` is set (see ``config.Settings``):

- **Auth ENABLED** (secret set): the tenant comes from a SIGNED login token
  (``Authorization: Bearer``), not a spoofable header. ``get_principal`` exposes the
  full identity; ``get_tenant_id`` returns just the tenant. The legacy
  ``X-Tenant-Id`` header is ignored unless ``allow_header_tenant`` is explicitly on.
- **Auth DISABLED** (empty secret): the platform falls back to the ``X-Tenant-Id``
  header stand-in — how dev/test run, so the existing header-based tests are
  unchanged. ``get_principal`` then 401s (there is no identity to resolve).

The tenant id these return is what scopes every database transaction (REQ-S1). The
capture-ingest path resolves its tenant separately (api/capture_router).
"""

from __future__ import annotations

import uuid
from functools import lru_cache

from fastapi import Header, HTTPException
from redis import Redis

from recon.auth import token as auth_token
from recon.auth.service import Principal
from recon.config import Settings, get_settings


@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url)


def _bearer_claims(authorization: str | None, settings: Settings) -> auth_token.AuthClaims | None:
    """Verify an ``Authorization: Bearer <auth-token>`` header, or ``None``.

    Returns ``None`` when auth is disabled, the header is absent/not Bearer, or the
    token fails verification (bad signature, wrong ``typ``, expired) — the token
    module fails closed on all attacker input.
    """
    if not settings.auth_secret or not authorization:
        return None
    scheme, _, raw = authorization.partition(" ")
    if scheme.lower() != "bearer" or not raw:
        return None
    return auth_token.verify(raw.strip(), key=settings.auth_secret)


def get_principal(authorization: str | None = Header(default=None)) -> Principal:
    """The authenticated identity, or 401. Use on routes that need the user/role.

    401s when auth is disabled (nothing to authenticate) or the token is
    missing/invalid — never falls back to the header, which carries no identity.
    """
    settings = get_settings()
    if not settings.auth_secret:
        raise HTTPException(status_code=401, detail="authentication is not configured")
    claims = _bearer_claims(authorization, settings)
    if claims is None:
        raise HTTPException(status_code=401, detail="a valid login is required")
    return Principal(user_id=claims.user_id, tenant_id=claims.tenant_id, role=claims.role)


def get_tenant_id(
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    authorization: str | None = Header(default=None),
) -> str:
    """The tenant scoping this request, from the login token (auth on) or the
    ``X-Tenant-Id`` header stand-in (auth off / header explicitly allowed)."""
    settings = get_settings()
    if settings.auth_secret:
        claims = _bearer_claims(authorization, settings)
        if claims is not None:
            return claims.tenant_id
        # Auth is on but there's no valid token. Only the explicit transition flag
        # lets the spoofable header back in; otherwise this is a hard 401.
        if not settings.allow_header_tenant:
            raise HTTPException(status_code=401, detail="a valid login is required")
    if not x_tenant_id:
        raise HTTPException(status_code=401, detail="X-Tenant-Id header required")
    # Canonicalize to a UUID string so RLS (which compares tenant_id::text) matches
    # regardless of header casing/format — a bad id must fail closed, loud.
    try:
        return str(uuid.UUID(x_tenant_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="X-Tenant-Id must be a UUID") from exc
