"""Coordinator — the seam between the state machine and the queues.

Knows which queue each stage runs on, creates the job row + enqueues the
message, and decides what to enqueue next when a stage finishes. Keeping this in
one place means the API and the worker never hand-wire queues themselves.
"""

from __future__ import annotations

from redis import Redis
from sqlalchemy import update

from recon import storage
from recon.config import get_settings
from recon.db.base import tenant_session
from recon.db.models import Job, Run
from recon.discover import queries as discover_queries
from recon.domain import AssetStatus, JobState, QueueName, RunStage, RunState
from recon.observability import get_logger
from recon.queue import streams
from recon.runs import assets as run_assets, service, state_machine as sm
from recon.runs.service import RunView

log = get_logger("recon.runs.coordinator")

# Which queue carries each stage's work (REQ-Q1). Ingest/analyze/correlate all
# run on the CPU-bound analyze pool.
STAGE_QUEUE: dict[RunStage, QueueName] = {
    RunStage.DISCOVERING: QueueName.DISCOVER,
    RunStage.FETCHING: QueueName.FETCH,
    RunStage.INGESTING: QueueName.ANALYZE,
    RunStage.ANALYZING: QueueName.ANALYZE,
    RunStage.CORRELATING: QueueName.ANALYZE,
}


def enqueue_stage(
    redis: Redis, *, tenant_id: str, run_id: str, stage: RunStage
) -> str:
    """Create the job row and put its message on the stage's queue.

    NOTE (follow-up, REQ-A3): the job row commit and the Redis enqueue are two
    steps. A crash between them strands a QUEUED job with no stream message. The
    slice-2 transactional outbox (which REQ-A3 requires for findings) will cover
    job enqueue too; until then a QUEUED-job reconciler sweep is the stopgap.
    """
    queue = STAGE_QUEUE[stage]
    max_attempts = get_settings().retry_max_attempts
    with tenant_session(tenant_id) as session:
        job = Job(
            tenant_id=tenant_id,
            run_id=run_id,
            queue=queue.value,
            stage=stage.value,
            state=JobState.QUEUED.value,
            max_attempts=max_attempts,
        )
        session.add(job)
        session.flush()
        job_id = str(job.id)
    streams.ensure_group(redis, queue)
    streams.enqueue(
        redis,
        queue,
        {
            "job_id": job_id,
            "run_id": str(run_id),
            "tenant_id": tenant_id,
            "queue": queue.value,
            "stage": stage.value,
            "attempts": 0,
            "max_attempts": max_attempts,
        },
    )
    return job_id


def start_run(
    redis: Redis,
    *,
    tenant_id: str,
    session_id: str,
    target: str | None = None,
    input_ref: str | None = None,
) -> RunView:
    """Create a run (returns immediately) and enqueue its first stage."""
    view = service.create_run(
        redis,
        tenant_id=tenant_id,
        session_id=session_id,
        target=target,
        input_ref=input_ref,
    )
    enqueue_stage(redis, tenant_id=tenant_id, run_id=view.id, stage=RunStage.DISCOVERING)
    return view


def start_run_with_input(
    redis: Redis,
    *,
    tenant_id: str,
    session_id: str,
    js_source: str | bytes,
    map_source: bytes | None = None,
    target: str | None = None,
) -> RunView:
    """Create a run, store its JS input (and optional source map) as blobs, point
    the run at them, then enqueue the first stage. The analyze stage reads the JS
    blob (REQ-D2) and, when present, recovers real source paths from the map."""
    content = js_source.encode("utf-8") if isinstance(js_source, str) else js_source
    view = service.create_run(redis, tenant_id=tenant_id, session_id=session_id, target=target)
    values: dict[str, str] = {"input_ref": storage.put_blob(tenant_id, view.id, "input", content)}
    if map_source:
        values["source_map_ref"] = storage.put_blob(tenant_id, view.id, "source_map", map_source)
    # Set the refs before enqueue so the analyze stage always sees them.
    with tenant_session(tenant_id) as session:
        session.execute(update(Run).where(Run.id == view.id).values(**values))
    enqueue_stage(redis, tenant_id=tenant_id, run_id=view.id, stage=RunStage.DISCOVERING)
    return view


def advance(redis: Redis, *, tenant_id: str, run_id: str, completed: RunStage) -> None:
    """A stage finished — enqueue the next one, or finalize the run."""
    nxt = sm.next_stage(completed)
    if nxt is not None:
        enqueue_stage(redis, tenant_id=tenant_id, run_id=run_id, stage=nxt)
        return
    try:
        to_state, completeness = _finalize_state(tenant_id, run_id)
        service.transition(
            redis,
            tenant_id=tenant_id,
            run_id=run_id,
            to_state=to_state,
            extra_values={"completeness": completeness},
        )

        # Fresh finalize only: a duplicate/concurrent delivery raises out of
        # `transition` above and lands in the `except` below, so a re-delivered
        # "stage finished" message never reaches this point and can't re-run
        # classification. Imported lazily (not at module load) to avoid a
        # recon.spec <-> recon.runs import cycle. Best-effort: a continuous
        # rescan (REQ-D5 gate N3) should keep new findings classified against
        # an already-attached spec without a manual re-POST, but classification
        # must never fail or roll back a run's finalize, so any error here is
        # logged and swallowed rather than raised.
        from recon.spec import service as spec_service

        try:
            spec_service.reclassify_run(tenant_id, run_id)
        except Exception:  # noqa: BLE001 - best-effort, must never fail finalize
            log.exception("runs.reclassify_failed", run_id=run_id, tenant_id=tenant_id)
    except (service.TransitionConflict, sm.InvalidTransition):
        # Already finalized by a concurrent/duplicate delivery — idempotent.
        pass


def _finalize_state(tenant_id: str, run_id: str) -> tuple[RunState, dict]:
    """DONE vs PARTIAL from per-asset status (REQ-D5).

    The discriminator for "is this a crawl run" is the presence of a
    discover.assets event, NOT the run_asset row count — a crawl that timed out
    before finding any assets has zero rows but must still finalize PARTIAL.
    Legacy single-asset runs (no discover.assets event) keep the historical
    hardcoded DONE.
    """
    event = discover_queries.latest_assets_event(tenant_id, run_id)
    if event is None:
        return RunState.DONE, {"fetch_ok": True, "analyze_ok": True}
    rows = run_assets.list_for_run(tenant_id, run_id)
    crawl_ok = event.get("status") == "ok"
    fetch_ok = crawl_ok and all(a.fetch_status == AssetStatus.OK.value for a in rows)
    analyze_ok = fetch_ok and all(a.analyze_status == AssetStatus.OK.value for a in rows)
    to_state = RunState.DONE if (fetch_ok and analyze_ok) else RunState.PARTIAL
    return to_state, {"fetch_ok": fetch_ok, "analyze_ok": analyze_ok}


def resume_run(redis: Redis, *, tenant_id: str, run_id: str) -> RunView:
    """Resume a paused run and re-enqueue the stage it left off at."""
    view, stage = service.resume(redis, tenant_id=tenant_id, run_id=run_id)
    enqueue_stage(redis, tenant_id=tenant_id, run_id=run_id, stage=stage)
    return view
