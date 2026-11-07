"""The worker's capture tick (``worker.main.serve_forever``): settle resting captures, and end
captures idle for ``IDLE_HOURS``.

A resting (CAPTURING) run with no live Job is settled when a pass is due (``reanalyze_after``
passed) or when it has been sealed (End, or anything that nulled its marker). Settling starts a
pass if a file is unanalyzed, finishes the run if it is sealed, and otherwise just clears the
schedule. Sealed-and-resting is a safety net as much as a feature: however a capture got there
(End racing a finalize, a lost publish), the next tick finishes it instead of leaving it
"capturing" forever.

Cross-tenant discovery uses the admin session, like the orphan reaper. Every claim and write goes
through the run's own tenant session. Each run is isolated: one that keeps failing is logged and
skipped, and never stops the tick (or the job processing that follows it).
"""

from __future__ import annotations

import datetime as dt

from redis import Redis
from sqlalchemy import func, or_, select

from recon.db.base import admin_session, tenant_session
from recon.db.models import Run, RunAsset
from recon.domain import RunStage, RunState
from recon.observability import get_logger
from recon.runs import capture_lifecycle, capture_pass, coordinator

log = get_logger("recon.runs.capture_sweep")

IDLE_HOURS = 24  # no new file this long → the capture ends itself
_LIMIT = 20  # runs per kind per tick, like the orphan reaper


def _settleable():
    """SQL predicate: a resting capture with no live pass that is due or sealed."""
    return (
        Run.state == RunState.CAPTURING.value,
        or_(Run.reanalyze_after < func.now(), Run.capture_external_id.is_(None)),
        ~capture_pass.live_job(Run.id),
    )


def sweep(redis: Redis) -> int:
    """One tick. Returns how many runs it acted on."""
    with admin_session() as session:
        due = session.execute(
            select(Run.id, Run.tenant_id).where(*_settleable()).limit(_LIMIT)
        ).all()
        last_file = (
            select(func.max(RunAsset.created_at)).where(RunAsset.run_id == Run.id).scalar_subquery()
        )
        idle = session.execute(
            select(Run.id, Run.tenant_id)
            .where(
                Run.capture_external_id.is_not(None),
                Run.state.in_([RunState.QUEUED.value, RunState.CAPTURING.value]),
                func.coalesce(last_file, Run.created_at)
                < func.now() - dt.timedelta(hours=IDLE_HOURS),
            )
            .limit(_LIMIT)
        ).all()
    acted = 0
    for run_id, tenant_id in due:
        try:
            acted += _settle_resting(redis, str(tenant_id), str(run_id))
        except Exception:  # noqa: BLE001 - one broken run must not stop the tick
            log.exception("capture.settle_failed", run_id=str(run_id))
    for run_id, tenant_id in idle:
        try:
            log.info("capture.auto_end_idle", run_id=str(run_id), idle_hours=IDLE_HOURS)
            capture_lifecycle.end_capture(redis, str(tenant_id), str(run_id), observations=[])
            acted += 1
        except Exception:  # noqa: BLE001
            log.exception("capture.auto_end_failed", run_id=str(run_id))
    return acted


def _settle_resting(redis: Redis, tenant_id: str, run_id: str) -> int:
    """Under the run-row lock (one worker at a time), with every check re-read after taking it: a
    concurrent End or Analyze may have queued a pass a moment before.

    - Scheduled (a quiet period passed, or End/Analyze asked during a pass) → one more pass, so
      late files AND late observations get analyzed.
    - Otherwise sealed → finished here. Otherwise (open, nothing scheduled) → nothing to do.
    """
    prepared = capture_pass.prepare(tenant_id, run_id, [])
    pending: capture_pass.Pending | None = None
    finish = False
    with tenant_session(tenant_id) as session:
        run = capture_pass.locked(session, run_id)
        if (
            run is None
            or RunState(run.state) != RunState.CAPTURING
            or capture_pass.live_job_exists(session, run_id)
        ):
            return 0
        scheduled = bool(
            session.scalar(select(Run.reanalyze_after < func.now()).where(Run.id == run_id))
        )
        sealed = run.capture_external_id is None
        if not (scheduled or sealed):
            return 0
        if prepared is not None and (scheduled or prepared.unanalyzed):
            pending = capture_pass.begin_pass(session, tenant_id, run, prepared.found)
        else:
            run.reanalyze_after = None
            finish = sealed
    capture_pass.publish_pending(redis, pending)
    if finish:
        coordinator.advance(
            redis, tenant_id=tenant_id, run_id=run_id, completed=RunStage.CORRELATING
        )
    log.info(
        "capture.settled",
        run_id=run_id,
        outcome="pass" if pending else "finished" if finish else "up_to_date",
    )
    return 1
