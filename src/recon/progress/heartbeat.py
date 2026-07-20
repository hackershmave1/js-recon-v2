"""Job progress + heartbeat (REQ-R1, REQ-R3).

A job writes a heartbeat as it works. The status endpoint compares the last
heartbeat against a threshold to tell "running slowly" apart from "stalled" —
the run isn't reported as still-working once its worker has gone quiet.
"""

from __future__ import annotations

import datetime as dt

from redis import Redis
from sqlalchemy import and_, func, or_, update

from recon.config import get_settings
from recon.db.base import tenant_session
from recon.db.models import Job
from recon.domain import JobState
from recon.events.log import emit


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _lease_until() -> dt.datetime:
    return _utcnow() + dt.timedelta(
        seconds=get_settings().heartbeat_stall_threshold_seconds
    )


def claim_job(tenant_id: str, job_id: str, *, total: int = 0) -> bool:
    """Atomically claim a job for processing — the worker's idempotency gate.

    A claim succeeds only if the job is fresh (queued), awaiting a retry
    (failed), or genuinely stalled (running but its lease expired). It fails for
    a job already succeeded/dead (a duplicate delivery) or one a live worker is
    still leasing — so a redelivered or reclaimed message can never re-run
    completed work or double-advance the run. Returns True if this worker won it.
    """
    with tenant_session(tenant_id) as session:
        result = session.execute(
            update(Job)
            .where(
                Job.id == job_id,
                or_(
                    Job.state.in_([JobState.QUEUED.value, JobState.FAILED.value]),
                    and_(
                        Job.state == JobState.RUNNING.value,
                        Job.lease_expires_at < func.now(),
                    ),
                ),
            )
            .values(
                state=JobState.RUNNING.value,
                heartbeat_at=_utcnow(),
                lease_expires_at=_lease_until(),
                total=total,
                done=0,
            )
        )
        return result.rowcount == 1


def beat(
    redis: Redis,
    *,
    tenant_id: str,
    run_id: str,
    job_id: str,
    done: int,
    total: int,
    eta_seconds: int | None = None,
    emit_event: bool = True,
) -> None:
    with tenant_session(tenant_id) as session:
        session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                heartbeat_at=_utcnow(),
                lease_expires_at=_lease_until(),
                done=done,
                total=total,
                eta_seconds=eta_seconds,
            )
        )
    if emit_event:
        emit(
            redis,
            tenant_id=tenant_id,
            run_id=run_id,
            event_type="job.progress",
            payload={"job_id": job_id, "done": done, "total": total, "eta_seconds": eta_seconds},
        )


def finish_job(
    tenant_id: str,
    job_id: str,
    state: JobState,
    *,
    attempts: int | None = None,
) -> bool:
    """Guarded finish: only a job this worker holds (RUNNING) can be finished.

    Returns False if another worker already resolved it — the caller then skips
    downstream work (e.g. advancing the run), so a duplicate cannot double-fire.
    """
    values: dict = {"state": state.value, "heartbeat_at": _utcnow()}
    if attempts is not None:
        values["attempts"] = attempts
    with tenant_session(tenant_id) as session:
        result = session.execute(
            update(Job)
            .where(Job.id == job_id, Job.state == JobState.RUNNING.value)
            .values(**values)
        )
        return result.rowcount == 1


def is_stalled(
    *, active: bool, heartbeat_at: dt.datetime | None, now: dt.datetime, threshold_s: float
) -> bool:
    """Pure: an active run whose heartbeat is older than the threshold is stalled."""
    if not active or heartbeat_at is None:
        return False
    return (now - heartbeat_at).total_seconds() > threshold_s
