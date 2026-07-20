"""Read models for run status (REQ-R1, REQ-R3, REQ-R4).

Kept apart from the write-side service so polling stays a cheap read. The status
view carries a strong ETag so ``If-None-Match`` polling returns 304 unchanged.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass

from sqlalchemy import desc, select

from recon.config import get_settings
from recon.db.base import tenant_session
from recon.db.models import Job, Run
from recon.domain import ACTIVE_STATES, RunState
from recon.progress.heartbeat import is_stalled


@dataclass(frozen=True)
class RunFlags:
    state: str
    stage: str | None
    pause_requested: bool
    cancel_requested: bool


def get_run_flags(tenant_id: str, run_id: str) -> RunFlags | None:
    """The control fields a worker checks at a safe checkpoint (REQ-A4)."""
    with tenant_session(tenant_id) as session:
        run = session.get(Run, run_id)
        if run is None:
            return None
        return RunFlags(
            state=run.state,
            stage=run.stage,
            pause_requested=run.pause_requested,
            cancel_requested=run.cancel_requested,
        )


@dataclass(frozen=True)
class StatusView:
    run_id: str
    state: str
    stage: str | None
    done: int
    total: int
    pct: int
    eta_seconds: int | None
    heartbeat_at: str | None
    stalled: bool
    etag: str


def _pct(done: int, total: int) -> int:
    return int(round(100 * done / total)) if total > 0 else 0


def get_status(tenant_id: str, run_id: str, *, now: dt.datetime | None = None) -> StatusView | None:
    now = now or dt.datetime.now(dt.timezone.utc)
    threshold = get_settings().heartbeat_stall_threshold_seconds
    with tenant_session(tenant_id) as session:
        run = session.get(Run, run_id)
        if run is None:
            return None
        job = session.scalars(
            select(Job).where(Job.run_id == run_id).order_by(desc(Job.created_at)).limit(1)
        ).first()
        done = job.done if job else 0
        total = job.total if job else 0
        eta = job.eta_seconds if job else None
        heartbeat = job.heartbeat_at if job else None
        active = RunState(run.state) in ACTIVE_STATES
        stalled = is_stalled(
            active=active, heartbeat_at=heartbeat, now=now, threshold_s=threshold
        )
        state = run.state
        stage = run.stage
    hb_iso = heartbeat.isoformat() if heartbeat else None
    raw = f"{state}:{stage}:{done}:{total}:{hb_iso}:{stalled}"
    etag = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return StatusView(
        run_id=str(run_id),
        state=state,
        stage=stage,
        done=done,
        total=total,
        pct=_pct(done, total),
        eta_seconds=eta,
        heartbeat_at=hb_iso,
        stalled=stalled,
        etag=etag,
    )
