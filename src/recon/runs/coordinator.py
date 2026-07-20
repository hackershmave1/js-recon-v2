"""Coordinator — the seam between the state machine and the queues.

Knows which queue each stage runs on, creates the job row + enqueues the
message, and decides what to enqueue next when a stage finishes. Keeping this in
one place means the API and the worker never hand-wire queues themselves.
"""

from __future__ import annotations

from redis import Redis

from recon.config import get_settings
from recon.db.base import tenant_session
from recon.db.models import Job
from recon.domain import JobState, QueueName, RunStage, RunState
from recon.queue import streams
from recon.runs import service, state_machine as sm
from recon.runs.service import RunView

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


def advance(redis: Redis, *, tenant_id: str, run_id: str, completed: RunStage) -> None:
    """A stage finished — enqueue the next one, or finalize the run."""
    nxt = sm.next_stage(completed)
    if nxt is not None:
        enqueue_stage(redis, tenant_id=tenant_id, run_id=run_id, stage=nxt)
        return
    try:
        service.transition(
            redis,
            tenant_id=tenant_id,
            run_id=run_id,
            to_state=RunState.DONE,
            extra_values={"completeness": {"fetch_ok": True, "analyze_ok": True}},
        )
    except (service.TransitionConflict, sm.InvalidTransition):
        # Already finalized by a concurrent/duplicate delivery — idempotent.
        pass


def resume_run(redis: Redis, *, tenant_id: str, run_id: str) -> RunView:
    """Resume a paused run and re-enqueue the stage it left off at."""
    view, stage = service.resume(redis, tenant_id=tenant_id, run_id=run_id)
    enqueue_stage(redis, tenant_id=tenant_id, run_id=run_id, stage=stage)
    return view
