"""Worker: consume jobs off the queues and drive the run through its stages.

Slice-1 stage work is a stub (a few heartbeating steps) so the async spine can
be exercised end-to-end before the real engines arrive. The worker observes
cancel/pause flags at safe checkpoints (REQ-A4), retries transient failures with
backoff, dead-letters exhausted ones (REQ-Q2), and reclaims work abandoned by a
crashed peer (REQ-R3 durability).
"""

from __future__ import annotations

import time

from redis import Redis

from recon.config import get_settings
from recon.domain import JobState, QueueName, RunStage, RunState
from recon.fetch import fetch
from recon.findings import analyze
from recon.observability import bind_run, get_logger
from recon.progress import heartbeat as progress
from recon.queue import retry, streams
from recon.runs import coordinator, queries, service
from recon.runs import state_machine as sm

log = get_logger("recon.worker")

# Queues a slice-1 worker serves (the stages we actually run).
SERVED_QUEUES: tuple[QueueName, ...] = (
    QueueName.DISCOVER,
    QueueName.FETCH,
    QueueName.ANALYZE,
)

STUB_STEPS = 4
STUB_STEP_DELAY_SECONDS = 0.0


def _to_paused(redis: Redis, tenant_id: str, run_id: str, stage: RunStage) -> None:
    service.transition(
        redis,
        tenant_id=tenant_id,
        run_id=run_id,
        to_state=RunState.PAUSED,
        extra_values={"resumed_from_stage": stage.value},
    )


def _to_cancelled(redis: Redis, tenant_id: str, run_id: str) -> None:
    service.transition(
        redis, tenant_id=tenant_id, run_id=run_id, to_state=RunState.CANCELLED
    )


def _enter_stage(redis: Redis, tenant_id: str, run_id: str, stage: RunStage, state: str) -> bool:
    """Move the run into ``stage`` unless it is already there. Returns False if
    the run can't legally enter (paused/cancelled/terminal) — caller drops the job."""
    if RunState(state) == sm.state_for_stage(stage):
        return True
    try:
        service.transition(
            redis,
            tenant_id=tenant_id,
            run_id=run_id,
            to_state=sm.state_for_stage(stage),
            stage=stage,
        )
        return True
    except (sm.InvalidTransition, service.TransitionConflict):
        return False


def process_message(redis: Redis, queue: QueueName, msg_id: str, message: dict) -> str:
    tenant_id = message["tenant_id"]
    run_id = message["run_id"]
    job_id = message["job_id"]
    stage = RunStage(message["stage"])
    attempts = int(message.get("attempts", 0))
    max_attempts = int(message.get("max_attempts", get_settings().retry_max_attempts))

    with bind_run(run_id, stage.value):
        flags = queries.get_run_flags(tenant_id, run_id)
        if flags is None:
            streams.ack(redis, queue, msg_id)
            return "gone"
        if sm.is_terminal(RunState(flags.state)):
            streams.ack(redis, queue, msg_id)
            return "skipped"
        if RunState(flags.state) == RunState.PAUSED:
            # Held — drop this delivery; resume re-enqueues the right stage.
            streams.ack(redis, queue, msg_id)
            return "paused"
        if flags.cancel_requested:
            _to_cancelled(redis, tenant_id, run_id)
            streams.ack(redis, queue, msg_id)
            return "cancelled"
        if flags.pause_requested:
            _to_paused(redis, tenant_id, run_id, stage)
            streams.ack(redis, queue, msg_id)
            return "paused"

        # Idempotency gate: exactly one worker may hold a job (REQ tenets:
        # idempotent & resumable). A duplicate/redelivered message for an
        # already-finished job loses the claim and is dropped.
        if not progress.claim_job(tenant_id, job_id, total=STUB_STEPS):
            streams.ack(redis, queue, msg_id)
            return "duplicate"

        if not _enter_stage(redis, tenant_id, run_id, stage, flags.state):
            streams.ack(redis, queue, msg_id)
            return "skipped"

        try:
            for step in range(1, STUB_STEPS + 1):
                control = queries.get_run_flags(tenant_id, run_id)
                if control and control.cancel_requested:
                    _to_cancelled(redis, tenant_id, run_id)
                    streams.ack(redis, queue, msg_id)
                    return "cancelled"
                if control and control.pause_requested:
                    _to_paused(redis, tenant_id, run_id, stage)
                    streams.ack(redis, queue, msg_id)
                    return "paused"
                if STUB_STEP_DELAY_SECONDS:
                    time.sleep(STUB_STEP_DELAY_SECONDS)
                progress.beat(
                    redis,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    job_id=job_id,
                    done=step,
                    total=STUB_STEPS,
                )
            # Real work. The fetch stage pulls the run's target asset through the
            # egress guard into the input blob; analyze extracts findings from it.
            # A failure in either routes to retry/DLQ.
            if stage == RunStage.FETCHING:
                fetch.fetch_run(redis, tenant_id=tenant_id, run_id=run_id)
            if stage == RunStage.ANALYZING:
                analyze.analyze_run(redis, tenant_id=tenant_id, run_id=run_id)
        except Exception as exc:  # noqa: BLE001 - failure routing is intentional
            return _handle_failure(
                redis, queue, msg_id, message, exc,
                tenant_id=tenant_id, run_id=run_id, job_id=job_id,
                stage=stage, attempts=attempts, max_attempts=max_attempts,
            )

        # Only advance if we still hold the job — guards against a duplicate that
        # somehow raced this far double-advancing the run.
        if progress.finish_job(tenant_id, job_id, JobState.SUCCEEDED):
            coordinator.advance(redis, tenant_id=tenant_id, run_id=run_id, completed=stage)
        streams.ack(redis, queue, msg_id)
        return "done"


def _handle_failure(
    redis: Redis, queue: QueueName, msg_id: str, message: dict, exc: Exception,
    *, tenant_id: str, run_id: str, job_id: str, stage: RunStage,
    attempts: int, max_attempts: int,
) -> str:
    settings = get_settings()
    attempt_no = attempts + 1
    if retry.should_retry(attempt_no, max_attempts, exc):
        delay = retry.compute_delay(
            attempt_no,
            base_delay=settings.retry_base_delay_seconds,
            max_delay=settings.retry_max_delay_seconds,
        )
        # A fetch politeness throttle / target Retry-After asks for a minimum wait
        # (REQ-Q3); never undercut it with a shorter backoff sample.
        retry_after = getattr(exc, "retry_after", None)
        if retry_after:
            delay = max(delay, float(retry_after))
        message["attempts"] = attempt_no
        progress.finish_job(tenant_id, job_id, JobState.FAILED, attempts=attempt_no)
        streams.reschedule(redis, queue, message, delay)
        streams.ack(redis, queue, msg_id)
        log.warning("job.retry", job_id=job_id, attempt=attempt_no, delay=round(delay, 2))
        return "retry"
    streams.to_dlq(redis, queue, message, error=str(exc))
    progress.finish_job(tenant_id, job_id, JobState.DEAD, attempts=attempt_no)
    service.transition(
        redis,
        tenant_id=tenant_id,
        run_id=run_id,
        to_state=RunState.FAILED,
        extra_values={"error": {"stage": stage.value, "message": str(exc)}},
    )
    streams.ack(redis, queue, msg_id)
    log.error("job.dead", job_id=job_id, error=str(exc))
    return "dead"


def run_once(
    redis: Redis, consumer: str, *, batch: int = 10, block_ms: int = 1000
) -> int:
    """One maintenance + drain pass across the served queues. Returns messages
    processed. Called in a loop by :func:`serve_forever`; tests call it directly."""
    stall_ms = int(get_settings().heartbeat_stall_threshold_seconds * 1000)
    processed = 0
    for queue in SERVED_QUEUES:
        streams.ensure_group(redis, queue)
        streams.promote_due(redis, queue)
        for msg_id, message in streams.reclaim_stalled(
            redis, queue, consumer, min_idle_ms=stall_ms, count=batch
        ):
            process_message(redis, queue, msg_id, message)
            processed += 1
        for msg_id, message in streams.read_batch(
            redis, queue, consumer, count=batch, block_ms=block_ms
        ):
            process_message(redis, queue, msg_id, message)
            processed += 1
        streams.trim_acked(redis, queue)
    return processed


def serve_forever(consumer: str = "worker-1") -> None:  # pragma: no cover
    redis = Redis.from_url(get_settings().redis_url)
    log.info("worker.started", consumer=consumer, queues=[q.value for q in SERVED_QUEUES])
    while True:
        try:
            run_once(redis, consumer)
        except Exception:  # noqa: BLE001 - keep the loop alive
            log.exception("worker.loop_error")
            time.sleep(1)


if __name__ == "__main__":  # pragma: no cover
    serve_forever()
