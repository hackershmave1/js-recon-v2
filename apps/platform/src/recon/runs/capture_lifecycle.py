"""The capture lifecycle: one capture (Start → End in the extension) is one run.

- ``start_capture`` opens the run, ending any capture the session left open.
- ``analyze`` ("Analyze now") runs an analysis pass and leaves the capture OPEN; the pass rests in
  the non-terminal CAPTURING state (``coordinator._finalize_state``).
- Files that land after the first analysis schedule another pass ``QUIET_SECONDS`` after the last
  batch (``note_batch``, in the batch's own transaction, behind ``accept_batch``). The worker's
  ``capture_sweep`` starts due passes, finishes sealed ones, and ends idle captures.
- ``end_capture`` seals the run (nulls the accumulator marker); the last pass, or a direct finish,
  turns it DONE/PARTIAL. Any terminal transition also releases the marker (``runs.service``).

Terminal states stay final: a run is never reopened (design: specs/2026-11-07-capture-lifecycle).
One caller wins each decision through ``SELECT … FOR UPDATE`` on the run row; the marker can't
elect it any more (DEBT D14) because it now stays set through Analyze.
"""

from __future__ import annotations

import datetime as dt

from redis import Redis
from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from recon.db.base import tenant_session
from recon.db.models import Job, Run, RunAsset
from recon.discover import manifest as discovery
from recon.domain import TERMINAL_STATES, RunStage, RunState
from recon.observability import get_logger
from recon.runs import capture_pass, coordinator, service
from recon.runs import state_machine as sm

log = get_logger("recon.runs.capture_lifecycle")

QUIET_SECONDS = 10  # uploads quiet this long → the next pass starts
_TERMINAL = [s.value for s in TERMINAL_STATES]


def find_open_run(tenant_id: str, ext_session_id: str) -> str | None:
    """The session's open capture, if any (its marker is the extension's session id)."""
    with tenant_session(tenant_id) as session:
        run_id = session.scalar(
            select(Run.id).where(
                Run.capture_external_id == ext_session_id, Run.state.not_in(_TERMINAL)
            )
        )
        return str(run_id) if run_id is not None else None


def start_capture(redis: Redis, tenant_id: str, session_id: str, ext_session_id: str) -> str:
    """Open a capture run for the session. A capture left open with files is ended first (it gets
    its final analysis instead of absorbing the next capture's files); one still EMPTY is simply
    reused, so two quick Starts never delete each other's capture."""
    left_open = find_open_run(tenant_id, ext_session_id)
    if left_open is not None:
        with tenant_session(tenant_id) as session:
            empty = not session.scalar(
                select(exists(select(RunAsset.id).where(RunAsset.run_id == left_open)))
            ) and not capture_pass.has_job(session, left_open)
        if empty:
            return left_open
        end_capture(redis, tenant_id, left_open, observations=[])
    try:
        view = service.create_run(
            redis,
            tenant_id=tenant_id,
            session_id=str(session_id),
            target=None,  # an upload run is never crawled or fetched
            capture_external_id=ext_session_id,
        )
    except IntegrityError:
        # A concurrent Start won the one-open-capture-per-session key; use its run.
        resolved = find_open_run(tenant_id, ext_session_id)
        if resolved is None:
            raise
        return resolved
    log.info("capture.started", run_id=view.id, ended_previous=left_open)
    return view.id


def accept_batch(session: Session, run_id: str, ext_session_id: str) -> bool:
    """In the batch's transaction, before its rows: is this still the session's open capture?
    The share lock waits out a concurrent End or Cancel, so a batch can never commit into a run
    that has just been sealed (where nothing would analyze it)."""
    run = session.scalar(select(Run).where(Run.id == str(run_id)).with_for_update(read=True))
    return (
        run is not None
        and run.capture_external_id == ext_session_id
        and RunState(run.state) not in TERMINAL_STATES
    )


def note_batch(session: Session, run_id: str) -> None:
    """A batch just landed (call inside the batch's transaction, so the files and their pending
    pass commit together). Once the run has been analyzed, each batch pushes the next pass to
    ``QUIET_SECONDS`` from now: one pass per burst of files, not one per batch."""
    session.execute(
        update(Run)
        .where(
            Run.id == str(run_id),
            Run.state.not_in(_TERMINAL),
            exists(select(Job.id).where(Job.run_id == Run.id)),
        )
        .values(reanalyze_after=func.now() + dt.timedelta(seconds=QUIET_SECONDS))
    )


def analyze(redis: Redis, tenant_id: str, run_id: str, *, observations: list[dict]) -> dict:
    """ "Analyze now". Starts a pass when one can start and there is something new; otherwise
    makes sure the files that arrived meanwhile get the next one. The capture stays open."""
    prepared = capture_pass.prepare(tenant_id, run_id, observations)
    if prepared is None:
        return {"started": False, "message": "no captured files to analyze", "runId": run_id}
    pending: capture_pass.Pending | None = None
    with tenant_session(tenant_id) as session:
        run = capture_pass.locked(session, run_id)
        if run is None or sm.is_terminal(RunState(run.state)):
            return {"started": False, "message": "capture ended", "runId": run_id}
        if capture_pass.can_start_pass(session, run):
            if RunState(run.state) == RunState.CAPTURING and not (
                prepared.unanalyzed or prepared.new_observations
            ):
                return {
                    "started": False,
                    "upToDate": True,
                    "message": "analysis is up to date",
                    "runId": run_id,
                }
            pending = capture_pass.begin_pass(session, tenant_id, run, prepared.found)
            message = "analysis started"
        else:
            event = discovery.record(
                session, tenant_id=tenant_id, run_id=run_id, found=prepared.found
            )
            run.reanalyze_after = func.now()  # due the moment the queued/running pass is done
            pending = (event, None)
            message = "analysis already running; new files follow"
    capture_pass.publish_pending(redis, pending)
    log.info("capture.analyze", run_id=run_id, message=message)
    return {"started": True, "message": message, "runId": run_id}


def end_capture(redis: Redis, tenant_id: str, run_id: str, *, observations: list[dict]) -> dict:
    """End: seal the capture and see it finished. Idempotent.

    - No files at all → the empty run is deleted: an empty capture leaves nothing behind.
    - Never analyzed, or resting with something new (files, observations, a scheduled pass)
      → the final pass starts; its finalize turns it DONE/PARTIAL.
    - Resting with nothing new → finished right here.
    - A pass queued or running → it is marked due again, so the sweeper settles the run if that
      pass's finalize read the capture as still open.
    """
    prepared = capture_pass.prepare(tenant_id, run_id, observations)
    pending: capture_pass.Pending | None = None
    finish = False
    with tenant_session(tenant_id) as session:
        run = capture_pass.locked(session, run_id)
        if run is None or sm.is_terminal(RunState(run.state)):
            return {"ended": True, "runId": run_id if run is not None else None}
        run.capture_external_id = None
        state = RunState(run.state)
        has_files = session.scalar(
            select(exists(select(RunAsset.id).where(RunAsset.run_id == run_id)))
        )
        if not has_files and not capture_pass.has_job(session, run_id):
            session.execute(delete(Run).where(Run.id == run_id))
            log.info("capture.ended_empty", run_id=run_id)
            return {"ended": True, "runId": None}
        if prepared is None:
            run.reanalyze_after = func.now()  # files landed after prepare(): the sweeper settles it
        elif capture_pass.can_start_pass(session, run):
            due = prepared.unanalyzed or prepared.new_observations or run.reanalyze_after
            if state != RunState.CAPTURING or due:
                pending = capture_pass.begin_pass(session, tenant_id, run, prepared.found)
            else:
                finish = True
        else:
            event = discovery.record(
                session, tenant_id=tenant_id, run_id=run_id, found=prepared.found
            )
            run.reanalyze_after = func.now()
            pending = (event, None)
    capture_pass.publish_pending(redis, pending)
    if finish:
        # Same finalize as a pass: CAPTURING → DONE/PARTIAL now that the marker is gone.
        coordinator.advance(
            redis, tenant_id=tenant_id, run_id=run_id, completed=RunStage.CORRELATING
        )
    log.info("capture.ended", run_id=run_id, finished_now=finish)
    return {"ended": True, "runId": run_id}
