"""Engagement-session endpoints (tenant-scoped).

Tenant creation is deliberately NOT here: it needs the privileged admin
connection, so it lives in the out-of-band bootstrap CLI (recon.bootstrap), not
on an anonymous request route. These routes are tenant-scoped by the central-login
session token (``api.deps.get_tenant_id``).

R6 adds the Sessions surface: list the tenant's sessions as cards (each with its
latest run's real stats), open a session's runs, and rename / archive / delete /
re-run. Isolation is the database's (RLS): a session absent for this tenant is a
404, deliberately distinct from an empty list.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, StringConstraints
from redis import Redis
from sqlalchemy.exc import IntegrityError

from recon.api.deps import get_actor, get_redis, get_tenant_id
from recon.findings import queries as findings_queries
from recon.runs import coordinator
from recon.sessions import bulk_delete, service

router = APIRouter(tags=["sessions"])


class CreateSessionBody(BaseModel):
    name: str | None = None
    scope_hosts: list[str] = Field(default_factory=list)
    authorized_by: str
    engagement_id: str | None = None
    # Optional crawl target: when scope_hosts is blank its host seeds the scope, so
    # the New Recon form doesn't make the user retype the domain (S3).
    target: str | None = None


# A package name or a whole scope: `lodash-es`, `@acme/api-client`, `@acme/*`.
_PACKAGE_PATTERN = r"^\s*(@[a-z0-9._~-]+/)?([a-z0-9._~-]+|\*)\s*$"


class PatchSessionBody(BaseModel):
    name: str | None = None
    archived: bool | None = None
    # Packages treated as APP code in this session (#181): not hidden as third-party.
    app_packages: (
        list[Annotated[str, StringConstraints(pattern=_PACKAGE_PATTERN, max_length=214)]] | None
    ) = Field(default=None, max_length=200)


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


@router.get("/sessions/{session_id}/findings/summary")
def get_session_findings_summary(session_id: str, tenant_id: str = Depends(get_tenant_id)) -> dict:
    """Lightweight findings summary for the latest completed run of a session.

    Returns the per-bucket counts and the top-3 highest-priority findings so the
    extension popup can render a compact card without fetching the full findings
    list. ``{"status": "no_run"}`` when the session has no terminal run yet (or the
    session is invisible to this tenant — RLS makes both cases indistinguishable,
    which is correct: a tenant must not learn that a session belonging to another
    tenant exists at all).
    """
    summary = findings_queries.get_session_findings_summary(tenant_id, session_id)
    if summary is None:
        # The extension sends its own UUID (external_id), not the platform UUID.
        # Fall back to an external_id lookup so the popup card works without the
        # caller needing to know the platform's internal UUID.
        platform_id = service.find_session_id_by_external_id(tenant_id, session_id)
        if platform_id is not None:
            summary = findings_queries.get_session_findings_summary(tenant_id, platform_id)
    if summary is None:
        return {"status": "no_run"}
    return {
        "status": "complete",
        "session_id": summary.session_id,
        "run_id": summary.run_id,
        "counts": {
            "total": summary.counts.total,
            "endpoints": summary.counts.endpoints,
            "secrets": summary.counts.secrets,
            "internal_ips": summary.counts.internal_ips,
            "graphql": summary.counts.graphql,
            "other": summary.counts.other,
        },
        "top_findings": [
            {"type": f.type, "value": f.value, "priority": f.priority} for f in summary.top_findings
        ],
    }


@router.patch("/sessions/{session_id}")
def patch_session(
    session_id: str, body: PatchSessionBody, tenant_id: str = Depends(get_tenant_id)
) -> dict:
    if body.name is None and body.archived is None and body.app_packages is None:
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
    if body.app_packages is not None:
        packages = list(dict.fromkeys(p.strip() for p in body.app_packages))
        view = service.set_app_packages(tenant_id, session_id, packages=packages)
        if view is None:
            raise HTTPException(status_code=404, detail="session not found")
    return _session_view_dict(view)


class BulkDeleteBody(BaseModel):
    # UUID-typed so a malformed id rejects the WHOLE batch (422) before anything is deleted.
    session_ids: list[uuid.UUID] = Field(min_length=1, max_length=bulk_delete.MAX_BATCH)


# Sync `def` like the single delete: psycopg2 + boto3 block, so FastAPI runs it in its threadpool.
@router.post("/sessions/bulk-delete")
def bulk_delete_sessions(
    body: BulkDeleteBody,
    tenant_id: str = Depends(get_tenant_id),
    actor: str | None = Depends(get_actor),
) -> dict:
    ids = list(dict.fromkeys(str(session_id) for session_id in body.session_ids))
    result = bulk_delete.delete_sessions(tenant_id, ids, actor)
    return {"deleted": result.deleted, "not_found": result.not_found, "failed": result.failed}


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(
    session_id: str,
    tenant_id: str = Depends(get_tenant_id),
    actor: str | None = Depends(get_actor),
) -> Response:
    if not service.delete_session(tenant_id, session_id, actor=actor):
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
        "failure_category": run.failure_category,
        "failure_reason": run.failure_reason,
    }
