"""Threat model routes — POST/GET /sessions/{id}/threat-model.

POST triggers async generation (idempotent: returns current state if already
running/pending). GET returns the current state plus threats once done.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException
from pydantic import BaseModel

from recon.api.deps import get_tenant_id
from recon.llm import run_options
from recon.threat_model import service

router = APIRouter(tags=["threat-model"])


class ThreatModelTriggerIn(BaseModel):
    preset: Literal["cheapest", "balanced", "strongest"] | None = None


@router.post("/sessions/{session_id}/threat-model", status_code=202)
async def trigger_threat_model(
    session_id: str,
    background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_tenant_id),
    body: ThreatModelTriggerIn | None = Body(default=None),
) -> dict:
    """Trigger (or re-trigger) threat model generation for the session.

    Returns 202 with the current state. If already pending/running, returns
    the existing state without starting a new task (idempotent)."""
    state = await _run_in_thread(service.trigger_generation, tenant_id, session_id)
    if not state:
        raise HTTPException(status_code=404, detail="session not found")
    # Start the task when pending (newly created, re-triggered, or orphaned after restart).
    # Skip when "running" — a task is already in flight. trigger_generation only returns
    # "running" when it found an existing in-progress row and left it unchanged.
    if state["status"] == "pending":
        background_tasks.add_task(
            service.run_generation, tenant_id, session_id, body.preset if body else None
        )
    return state


@router.get("/sessions/{session_id}/threat-model")
async def get_threat_model(
    session_id: str,
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    """Return the current threat model state and threats for the session."""
    result = await _run_in_thread(service.get_threat_model, tenant_id, session_id)
    if result is None:
        raise HTTPException(status_code=404, detail="no threat model for this session")
    return result


@router.get("/sessions/{session_id}/threat-model/presets")
async def get_threat_model_presets(
    session_id: str,
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    """The model each preset would run for this session's credential (no key material)."""
    return await _run_in_thread(run_options.session_preset_options, tenant_id, session_id)


# ---------------------------------------------------------------------------
# Helper — run sync service calls without blocking the event loop
# ---------------------------------------------------------------------------

from fastapi.concurrency import run_in_threadpool


async def _run_in_thread(fn, *args):
    return await run_in_threadpool(fn, *args)
