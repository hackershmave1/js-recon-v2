from __future__ import annotations

from datetime import datetime
from typing import Iterable

from sqlalchemy.orm import Session

from ..models import Job as DbJob


ACTIVE_JOB_STATUSES = ("queued", "running", "cancelling")


def recover_orphaned_jobs(
    db: Session,
    *,
    active_statuses: Iterable[str] = ACTIVE_JOB_STATUSES,
    recovered_at: datetime | None = None,
) -> int:
    """Mark persisted jobs left active by a previous process as terminal."""
    timestamp = recovered_at or datetime.utcnow()
    jobs = db.query(DbJob).filter(DbJob.status.in_(tuple(active_statuses))).all()

    for job in jobs:
        state = dict(job.state_json or {})
        if job.cancel_requested:
            job.status = "cancelled"
            job.error = job.error or "Job cancellation finalized during startup recovery"
        else:
            job.status = "failed"
            job.error = job.error or "Job marked stale during startup recovery after previous worker exit"

        job.finished_at = timestamp
        state["status"] = job.status
        state["startup_recovered"] = True
        state["startup_recovered_at"] = timestamp.isoformat()
        state["error"] = job.error
        job.state_json = state

    if jobs:
        db.commit()

    return len(jobs)
