"""The Hosts page's scope panel endpoints (ADR-0010, S3). Workspace-only (tenant-scoped).

* ``GET    /sessions/{id}/scope``: scope list, undecided hosts (files, 7-day expiry), the ignore list
  and the hidden (removed-from-scope) hosts with their file and finding counts.
* ``POST   /sessions/{id}/scope``  ``{"add": host}``: add to the scope; a host on the ignore list is
  taken off it (the researcher changed their mind).
* ``DELETE /sessions/{id}/scope/{host}``: remove from the scope. Hides, never deletes.
* ``DELETE /sessions/{id}/hosts/{host}/content``: the explicit delete of a removed host's content.

Add and Ignore of an UNDECIDED host are ``POST /sessions/{id}/hosts/{host}/decision`` (quarantine).
"""

from __future__ import annotations

import contextlib

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from recon.api.deps import get_tenant_id
from recon.quarantine import scope_view, service
from recon.sessions import capture_scope

router = APIRouter(tags=["scope"])


class ScopeAddIn(BaseModel):
    add: str


@router.get("/sessions/{session_id}/scope")
def get_scope(session_id: str, tenant_id: str = Depends(get_tenant_id)) -> dict:
    view = scope_view.overview(tenant_id, session_id)
    if view is None:
        raise HTTPException(status_code=404, detail="session not found")
    return view


@router.post("/sessions/{session_id}/scope")
def add_scope(session_id: str, body: ScopeAddIn, tenant_id: str = Depends(get_tenant_id)) -> dict:
    try:
        scope = capture_scope.add_scope_entry(tenant_id, session_id, body.add)
    except capture_scope.InvalidScopeEntry as exc:
        raise HTTPException(status_code=422, detail="not a usable host") from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    # An entry the quarantine feature cannot name as a host is not on its ignore list either.
    with contextlib.suppress(service.QuarantineError):
        scope_view.forget_ignored(tenant_id, session_id, body.add)
    return {"scope_hosts": scope}


@router.delete("/sessions/{session_id}/scope/{host}")
def remove_scope(session_id: str, host: str, tenant_id: str = Depends(get_tenant_id)) -> dict:
    try:
        scope = capture_scope.remove_scope_entry(tenant_id, session_id, host)
    except capture_scope.InvalidScopeEntry as exc:
        raise HTTPException(status_code=422, detail="not a usable host") from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    return {"scope_hosts": scope}


@router.delete("/sessions/{session_id}/hosts/{host}/content")
def delete_content(session_id: str, host: str, tenant_id: str = Depends(get_tenant_id)) -> dict:
    try:
        return scope_view.delete_host_content(tenant_id, session_id, host)
    except scope_view.HostInScope as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "host_in_scope", "hint": "remove it from the scope first"},
        ) from exc
    except service.QuarantineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
