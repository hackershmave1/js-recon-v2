"""Engagement-session endpoints (tenant-scoped).

Tenant creation is deliberately NOT here: it needs the privileged admin
connection, so it lives in the out-of-band bootstrap CLI (recon.bootstrap), not
on an anonymous request route. Real auth for these routes lands in a later slice.

R6 adds the Sessions surface: list the tenant's sessions as cards (each with its
latest run's real stats), open a session's runs, and rename / archive / delete /
re-run. Isolation is the database's (RLS): a session absent for this tenant is a
404, deliberately distinct from an empty list.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from redis import Redis
from sqlalchemy.exc import IntegrityError

from recon.api.deps import get_redis, get_tenant_id
from recon.runs import coordinator
from recon.sessions import service

router = APIRouter(tags=["sessions"])


class CreateSessionBody(BaseModel):
    name: str | None = None
    scope_hosts: list[str] = Field(default_factory=list)
    authorized_by: str
    engagement_id: str | None = None
    # Optional crawl target: when scope_hosts is blank its host seeds the scope, so
    # the New Recon form doesn't make the user retype the domain (S3).
    target: str | None = None


class PatchSessionBody(BaseModel):
    name: str | None = None
    archived: bool | None = None


@router.post("/sessions", status_code=201)
def create_session(body: CreateSessionBody, tenant_id: str = Depends(get_tenant_id)) -> dict:
    try:
        view = service.create_session(
            tenant_id,
            name=body.name,
            scope_hosts=body.scope_hosts,
            authorized_by=body.authorized_by,
            engagement_id=body.engagement_id,
            target=body.target,
        )
    except (service.AuthorizationRequired, service.SessionInvalid) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        # A syntactically-valid tenant id that isn't provisioned violates the FK.
        raise HTTPException(status_code=400, detail="unknown tenant") from exc
    return _session_view_dict(view)


@router.get("/sessions")
def list_sessions(archived: bool = False, tenant_id: str = Depends(get_tenant_id)) -> dict:
    summaries = service.list_sessions(tenant_id, include_archived=archived)
    return {
        "count": len(summaries),
        "sessions": [_summary_dict(summary) for summary in summaries],
    }


@router.get("/sessions/{session_id}/runs")
def list_session_runs(session_id: str, tenant_id: str = Depends(get_tenant_id)) -> dict:
    runs = service.list_runs_for_session(tenant_id, session_id)
    if runs is None:
        raise HTTPException(status_code=404, detail="session not found")
    return {
        "session_id": session_id,
        "count": len(runs),
        "runs": [_run_ref_dict(run) for run in runs],
    }


@router.patch("/sessions/{session_id}")
def patch_session(
    session_id: str, body: PatchSessionBody, tenant_id: str = Depends(get_tenant_id)
) -> dict:
    if body.name is None and body.archived is None:
        raise HTTPException(status_code=400, detail="nothing to update")
    view = None
    if body.name is not None:
        try:
            view = service.rename_session(tenant_id, session_id, name=body.name)
        except service.SessionInvalid as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if view is None:
            raise HTTPException(status_code=404, detail="session not found")
    if body.archived is not None:
        view = service.set_session_archived(tenant_id, session_id, archived=body.archived)
        if view is None:
            raise HTTPException(status_code=404, detail="session not found")
    return _session_view_dict(view)


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str, tenant_id: str = Depends(get_tenant_id)) -> Response:
    if not service.delete_session(tenant_id, session_id):
        raise HTTPException(status_code=404, detail="session not found")
    return Response(status_code=204)


@router.post("/sessions/{session_id}/rerun", status_code=202)
def rerun_session(
    session_id: str,
    tenant_id: str = Depends(get_tenant_id),
    redis: Redis = Depends(get_redis),
) -> dict:
    if service.get_session(tenant_id, session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    try:
        view = coordinator.rerun(redis, tenant_id=tenant_id, session_id=session_id)
    except coordinator.NoRunToRerun as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except coordinator.CaptureModeUnavailable as exc:
        # A capture session re-run when the kill-switch is now off: a clean 400, not a
        # silent static downgrade (the old crawl_mode-drop bug) or a worker DLQ.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"run_id": view.id, "state": view.state}


def _session_view_dict(view: service.SessionView) -> dict:
    return {
        "session_id": view.id,
        "name": view.name,
        "scope_hosts": view.scope_hosts,
        "authorization_ack": view.authorization_ack,
        "created_at": view.created_at,
        "engagement_id": view.engagement_id,
        "archived": view.archived,
    }


def _summary_dict(summary: service.SessionSummary) -> dict:
    return {
        "session_id": summary.id,
        "external_id": summary.external_id,
        "name": summary.name,
        "host": summary.host,
        "scope_hosts": summary.scope_hosts,
        "engagement_id": summary.engagement_id,
        "archived": summary.archived,
        "created_at": summary.created_at,
        "latest_run": (_run_ref_dict(summary.latest_run) if summary.latest_run else None),
        "files": summary.files,
        "endpoints": summary.endpoints,
        "secrets": summary.secrets,
        "coverage_pct": summary.coverage_pct,
    }


def _run_ref_dict(run: service.RunRefView) -> dict:
    return {
        "run_id": run.run_id,
        "state": run.state,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "ended_at": run.ended_at,
        "target": run.target,
    }
