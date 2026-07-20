"""Engagement-session endpoints (tenant-scoped).

Tenant creation is deliberately NOT here: it needs the privileged admin
connection, so it lives in the out-of-band bootstrap CLI (recon.bootstrap), not
on an anonymous request route. Real auth for these routes lands in a later slice.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from recon.api.deps import get_tenant_id
from recon.sessions import service

router = APIRouter(tags=["sessions"])


class CreateSessionBody(BaseModel):
    name: str | None = None
    scope_hosts: list[str] = Field(default_factory=list)
    authorized_by: str


@router.post("/sessions", status_code=201)
def create_session(
    body: CreateSessionBody, tenant_id: str = Depends(get_tenant_id)
) -> dict:
    try:
        view = service.create_session(
            tenant_id,
            name=body.name,
            scope_hosts=body.scope_hosts,
            authorized_by=body.authorized_by,
        )
    except service.AuthorizationRequired as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "session_id": view.id,
        "scope_hosts": view.scope_hosts,
        "authorization_ack": view.authorization_ack,
    }
