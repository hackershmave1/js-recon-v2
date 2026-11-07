"""The capture lifecycle: one capture (Start → End in the extension) is one run.

- ``start_capture`` opens the run, ending any capture the session left open.
- ``analyze`` ("Analyze now") runs an analysis pass and leaves the capture OPEN. The pass rests in
  the non-terminal CAPTURING state (``coordinator._finalize_state``).
- Files that land after the first analysis schedule another pass ``QUIET_SECONDS`` after the last
  batch (``note_batch``, written in the batch's own transaction). ``sweep`` starts due passes.
- ``end_capture`` seals the run (nulls the accumulator marker). The last pass, or a direct finish,
  turns it DONE/PARTIAL. ``sweep`` also ends a capture idle for ``IDLE_HOURS``.

Terminal states stay final: a run is never reopened (design: specs/2026-11-07-capture-lifecycle).

One caller wins each decision through ``SELECT … FOR UPDATE`` on the run row. The marker can no
longer elect the winner the way it did for the old seal-on-analyze path (DEBT D14), because it now
stays set through Analyze. Manifest and observation blobs are written BEFORE the lock, so the row
lock never spans an S3 round trip. They are content-addressed, so a loser's write is harmless.
"""

from __future__ import annotations

import datetime as dt
import json
from urllib.parse import urlsplit

from redis import Redis
from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from recon import storage
from recon.db.base import admin_session, tenant_session
from recon.db.models import Job, Run, RunAsset
from recon.discover import manifest as discovery
from recon.domain import AssetStatus, DiscoveryStatus, RunStage, RunState
from recon.events.log import RecordedEvent, publish
from recon.observability import get_logger
from recon.runs import assets, coordinator, service
from recon.runs import state_machine as sm

log = get_logger("recon.runs.capture_lifecycle")

QUIET_SECONDS = 10  # uploads quiet this long → the next pass starts
IDLE_HOURS = 24  # no new file this long → the capture ends itself
_SWEEP_LIMIT = 20  # runs per kind per tick, like the orphan reaper

_Pending = tuple[RecordedEvent, dict | None]  # (discover.assets event, job message) to publish


def find_open_run(tenant_id: str, ext_session_id: str) -> str | None:
    """The session's open capture, if any (its marker is the extension's session id)."""
    with tenant_session(tenant_id) as session:
        run_id = session.scalar(select(Run.id).where(Run.capture_external_id == ext_session_id))
        return str(run_id) if run_id is not None else None


def start_capture(redis: Redis, tenant_id: str, session_id: str, ext_session_id: str) -> str:
    """Open a new capture run for the session, ending one left open first: a forgotten capture
    gets its final analysis instead of silently absorbing the next one's files."""
    left_open = find_open_run(tenant_id, ext_session_id)
    if left_open is not None:
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
        # A concurrent start won the one-open-capture-per-session key; use its run.
        resolved = find_open_run(tenant_id, ext_session_id)
        if resolved is None:
            raise
        return resolved
    log.info("capture.started", run_id=view.id, ended_previous=left_open)
    return view.id


def note_batch(session: Session, run_id: str) -> None:
    """A batch just landed (call inside the batch's transaction, so the files and their pending
    pass commit together). Once the run has been analyzed, each batch pushes the next pass to
    ``QUIET_SECONDS`` from now: one pass per burst of files, not one per batch."""
    session.execute(
        update(Run)
        .where(Run.id == str(run_id), exists(select(Job.id).where(Job.run_id == Run.id)))
        .values(reanalyze_after=func.now() + dt.timedelta(seconds=QUIET_SECONDS))
    )


def analyze(redis: Redis, tenant_id: str, run_id: str, *, observations: list[dict]) -> dict:
    """ "Analyze now": start a pass if none is queued or running; otherwise make sure the files
    that arrived meanwhile get the next one. The capture stays open either way."""
    prepared = _manifest(tenant_id, run_id, observations)
    if prepared is None:
        return {"started": False, "message": "no captured files to analyze", "runId": run_id}
    found, _ = prepared
    pending: _Pending | None = None
    with tenant_session(tenant_id) as session:
        run = _locked(session, run_id)
        if run is None or sm.is_terminal(RunState(run.state)):
            return {"started": False, "message": "capture ended", "runId": run_id}
        if _can_start_pass(session, run):
            pending = _begin_pass(session, tenant_id, run, found)
            message = "analysis started"
        else:
            event = discovery.record(session, tenant_id=tenant_id, run_id=run_id, found=found)
            run.reanalyze_after = func.now()  # due the moment the running pass finishes
            pending = (event, None)
            message = "analysis already running; new files follow"
    _publish(redis, pending)
    log.info("capture.analyze", run_id=run_id, message=message)
    return {"started": True, "message": message, "runId": run_id}


def end_capture(redis: Redis, tenant_id: str, run_id: str, *, observations: list[dict]) -> dict:
    """End: seal the capture and see it finished. Idempotent.

    - Never analyzed → its first (and final) pass starts.
    - Resting with unanalyzed files → the final pass starts; its finalize turns it DONE/PARTIAL.
    - Resting with nothing new → finished right here.
    - A pass queued or running → that pass's finalize sees the seal and finishes it.
    - No files at all → the empty run is deleted: an empty capture leaves nothing behind.
    """
    prepared = _manifest(tenant_id, run_id, observations)
    pending: _Pending | None = None
    finish = False
    with tenant_session(tenant_id) as session:
        run = _locked(session, run_id)
        if run is None or sm.is_terminal(RunState(run.state)):
            return {"ended": True, "runId": run_id}
        run.capture_external_id = None
        state = RunState(run.state)
        if prepared is None:
            if not _has_job(session, run_id):
                session.execute(delete(Run).where(Run.id == run_id))
                log.info("capture.ended_empty", run_id=run_id)
                return {"ended": True, "runId": None}
        else:
            found, unanalyzed = prepared
            due = unanalyzed or run.reanalyze_after is not None
            if _can_start_pass(session, run) and (state != RunState.CAPTURING or due):
                pending = _begin_pass(session, tenant_id, run, found)
            else:
                event = discovery.record(session, tenant_id=tenant_id, run_id=run_id, found=found)
                pending = (event, None)
                finish = state == RunState.CAPTURING
    _publish(redis, pending)
    if finish:
        # Same finalize as a pass: CAPTURING → DONE/PARTIAL now that the marker is gone.
        coordinator.advance(
            redis, tenant_id=tenant_id, run_id=run_id, completed=RunStage.CORRELATING
        )
    log.info("capture.ended", run_id=run_id, finished_now=finish)
    return {"ended": True, "runId": run_id}


def sweep(redis: Redis) -> int:
    """One worker tick: start the passes that are due, and end captures idle for a day.
    Cross-tenant discovery uses the admin session (like the orphan reaper); every claim and
    write goes through the run's own tenant session."""
    with admin_session() as session:
        due = session.execute(
            select(Run.id, Run.tenant_id)
            .where(Run.state == RunState.CAPTURING.value, Run.reanalyze_after < func.now())
            .limit(_SWEEP_LIMIT)
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
            .limit(_SWEEP_LIMIT)
        ).all()
    started = sum(_start_due_pass(redis, str(t), str(r)) for r, t in due)
    for run_id, tenant_id in idle:
        log.info("capture.auto_end_idle", run_id=str(run_id), idle_hours=IDLE_HOURS)
        end_capture(redis, str(tenant_id), str(run_id), observations=[])
    return started + len(idle)


def _start_due_pass(redis: Redis, tenant_id: str, run_id: str) -> int:
    prepared = _manifest(tenant_id, run_id, [])
    with tenant_session(tenant_id) as session:
        # Guarded claim: only the tick whose UPDATE matches starts the pass.
        claimed = session.execute(
            update(Run)
            .where(
                Run.id == run_id,
                Run.state == RunState.CAPTURING.value,
                Run.reanalyze_after < func.now(),
            )
            .values(reanalyze_after=None)
        )
        if claimed.rowcount != 1 or prepared is None:
            return 0
        run = _locked(session, run_id)
        assert run is not None  # just claimed under this transaction
        pending = _begin_pass(session, tenant_id, run, prepared[0])
    _publish(redis, pending)
    log.info("capture.pass_started", run_id=run_id, trigger="quiet")
    return 1


def _locked(session: Session, run_id: str) -> Run | None:
    return session.scalar(select(Run).where(Run.id == str(run_id)).with_for_update())


def _has_job(session: Session, run_id: str) -> bool:
    return bool(session.scalar(select(exists(select(Job.id).where(Job.run_id == str(run_id))))))


def _can_start_pass(session: Session, run: Run) -> bool:
    """No pass is queued or running: the run rests, or was never analyzed."""
    state = RunState(run.state)
    return state == RunState.CAPTURING or (
        state == RunState.QUEUED and not _has_job(session, str(run.id))
    )


def _begin_pass(session: Session, tenant_id: str, run: Run, found: discovery.Discovery) -> _Pending:
    """In the caller's transaction: the pass's manifest event + its DISCOVERING Job. The worker
    moves the run QUEUED/CAPTURING → DISCOVERING when it picks the job up."""
    event = discovery.record(session, tenant_id=tenant_id, run_id=str(run.id), found=found)
    run.reanalyze_after = None
    _, message = coordinator.create_stage_job(
        session, tenant_id=tenant_id, run_id=str(run.id), stage=RunStage.DISCOVERING
    )
    return event, message


def _publish(redis: Redis, pending: _Pending | None) -> None:
    """After commit: a subscriber must never see an unpersisted event or job."""
    if pending is None:
        return
    event, message = pending
    publish(redis, event)
    if message is not None:
        coordinator.publish_stage_job(redis, message)


def _manifest(
    tenant_id: str, run_id: str, observations: list[dict]
) -> tuple[discovery.Discovery, bool] | None:
    """The run's current manifest (every captured file) with every observation so far merged in,
    and whether any file is still unanalyzed. ``None`` when the run has no files."""
    rows = assets.list_for_run(tenant_id, run_id)
    if not rows:
        return None
    merged = _merged_observations(discovery.read_latest(tenant_id, run_id), observations)
    found = discovery.store_manifest(
        tenant_id=tenant_id,
        run_id=run_id,
        domain=next((h for h in (urlsplit(r.url).hostname for r in rows) if h), run_id),
        status=DiscoveryStatus.OK,
        asset_urls=[r.url for r in rows],
        source="extension",
        observed_requests=merged or None,
    )
    unanalyzed = any(
        r.fetch_status == AssetStatus.OK.value and r.analyze_status == AssetStatus.PENDING.value
        for r in rows
    )
    return found, unanalyzed


def _merged_observations(previous: discovery.Discovery | None, new: list[dict]) -> list[dict]:
    """Earlier observations first, then new ones not seen before. Correlate reads only the
    LATEST manifest, so every manifest must carry the whole set (first sighting wins)."""
    merged: list[dict] = []
    if previous is not None and previous.requests_ref:
        merged = list(json.loads(storage.get_blob(previous.requests_ref)))
    seen = {(o.get("method"), o.get("url")) for o in merged}
    for item in new:
        key = (item.get("method"), item.get("url"))
        if key not in seen:
            seen.add(key)
            merged.append(item)
    return merged
