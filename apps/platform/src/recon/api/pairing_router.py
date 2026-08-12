"""Operator-side pairing mint — issues a stateless signed token the Chrome extension
pastes so its captures route into THIS tenant instead of the shared capture tenant.

Operator-authenticated (``X-Tenant-Id``), so the capability is only creatable by the
tenant it grants. There is deliberately no revoke endpoint: the token is stateless
(``recon.pairing.token``), so "revoke" = rotate ``RECON_PAIRING_KEY`` (invalidates all).
Mounted only when ``enable_capture_ingest`` is on — the ingest that honors the token
(``api/capture_router._resolve_ingest_tenant``).
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from recon.api.deps import get_tenant_id
from recon.config import get_settings
from recon.db.base import admin_session
from recon.db.models import Tenant
from recon.pairing import token as pairing_token

router = APIRouter(tags=["pairing"])


@router.post("/pairing")
def mint_pairing(tenant_id: str = Depends(get_tenant_id)) -> dict:
    """Mint a pairing token for the caller's tenant (503 if pairing is unconfigured)."""
    settings = get_settings()
    if not settings.pairing_key:
        raise HTTPException(status_code=503, detail="pairing is not configured")
    # The token names a tenant; refuse to mint for one that does not exist (a mistyped or
    # since-deleted X-Tenant-Id) so a paired extension can't stream into a dangling id
    # (the ingest's session/run inserts would FK-fail). Existence check via admin (get_tenant_id
    # only canonicalizes the UUID, it does not confirm the row).
    with admin_session() as session:
        if session.scalar(select(Tenant.id).where(Tenant.id == tenant_id)) is None:
            raise HTTPException(status_code=404, detail="unknown tenant")
    ttl = settings.pairing_ttl_seconds
    token = pairing_token.mint(tenant_id, key=settings.pairing_key, ttl_seconds=ttl)
    return {"token": token, "ttlSeconds": ttl, "expiresAt": int(time.time()) + ttl}
