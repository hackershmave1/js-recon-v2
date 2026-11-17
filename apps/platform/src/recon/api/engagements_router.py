"""Engagement endpoints (tenant-scoped) — the scope umbrella grouping sessions (R6).

A thin list/create/delete over :mod:`recon.engagements.service`. Isolation is the
database's (RLS): engagements created under one tenant are invisible to another.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from recon.api.deps import get_actor, get_tenant_id
from recon.engagements import service

router = APIRouter(tags=["engagements"])


class CreateEngagementBody(BaseModel):
    name: str
    in_scope_domains: list[str] = Field(default_factory=list)
    out_of_scope_domains: list[str] = Field(default_factory=list)


@router.get("/engagements")
def list_engagements(tenant_id: str = Depends(get_tenant_id)) -> dict:
    views = service.list_engagements(tenant_id)
    return {"count": len(views), "engagements": [_view_dict(view) for view in views]}


@router.post("/engagements", status_code=201)
def create_engagement(body: CreateEngagementBody, tenant_id: str = Depends(get_tenant_id)) -> dict:
    try:
        view = service.create_engagement(
            tenant_id,
            name=body.name,
            in_scope_domains=body.in_scope_domains,
            out_of_scope_domains=body.out_of_scope_domains,
        )
    except service.EngagementInvalid as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        # A syntactically-valid tenant id that isn't provisioned violates the FK.
        raise HTTPException(status_code=400, detail="unknown tenant") from exc
    return _view_dict(view)


@router.delete("/engagements/{engagement_id}")
def delete_engagement(
    engagement_id: str,
    tenant_id: str = Depends(get_tenant_id),
    actor: str | None = Depends(get_actor),
) -> dict:
    """Delete the engagement; its sessions move to "No engagement" (never deleted)."""
    detached = service.delete_engagement(tenant_id, engagement_id, actor=actor)
    if detached is None:
        raise HTTPException(status_code=404, detail="engagement not found")
    return {"engagement_id": engagement_id, "detached_sessions": detached}


def _view_dict(view: service.EngagementView) -> dict:
    return {
        "engagement_id": view.id,
        "name": view.name,
        "in_scope_domains": view.in_scope_domains,
        "out_of_scope_domains": view.out_of_scope_domains,
        "created_at": view.created_at,
        "updated_at": view.updated_at,
        "session_count": view.session_count,
    }
