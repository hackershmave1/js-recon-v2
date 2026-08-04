"""FastAPI dependencies: the shared Redis client and tenant resolution.

Slice 1 resolves the tenant from an ``X-Tenant-Id`` header — a stand-in for real
auth, which arrives in a later slice. The tenant id it returns is what scopes
every database transaction (REQ-S1).
"""

from __future__ import annotations

import uuid
from functools import lru_cache

from fastapi import Header, HTTPException
from redis import Redis

from recon.config import get_settings


@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url)


def get_tenant_id(
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> str:
    if not x_tenant_id:
        raise HTTPException(status_code=401, detail="X-Tenant-Id header required")
    # Canonicalize to a UUID string so RLS (which compares tenant_id::text)
    # matches regardless of header casing/format — a bad id must fail closed, loud.
    try:
        return str(uuid.UUID(x_tenant_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="X-Tenant-Id must be a UUID") from exc
