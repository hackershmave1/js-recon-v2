"""End-to-end slice-1 verification against live Postgres + Redis.

Covers the slice-1 exit criteria: full run to done, atomic transitions (REQ-A2),
tenant isolation enforced in the database (REQ-S1), pause/resume and cancel
(REQ-A4), and failure -> DLQ + FAILED (REQ-Q2). Requires the stack:

    docker compose up -d
    alembic upgrade head   # (done automatically by the `migrated` fixture)
    pytest -m integration
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import text

from recon.db.base import SessionLocal
from recon.domain import RunStage, RunState
from recon.progress import heartbeat as progress
from recon.queue import retry, streams
from recon.runs import coordinator, queries, service
from recon.runs import state_machine as sm
from recon.sessions import service as sessions_service
from recon.worker import main as worker

pytestmark = pytest.mark.integration


def _drive(redis, run_id, tenant, *, max_passes=30) -> str:
    for _ in range(max_passes):
        worker.run_once(redis, "test-worker", block_ms=50)
        status = queries.get_status(tenant, run_id)
        if status and RunState(status.state) in {
            RunState.DONE, RunState.PARTIAL, RunState.FAILED, RunState.CANCELLED, RunState.PAUSED
        }:
            return status.state
    return queries.get_status(tenant, run_id).state


def test_full_run_reaches_done(redis, authorized_session):
    tenant, session_id = authorized_session
    view = coordinator.start_run(redis, tenant_id=tenant, session_id=session_id, target="acme.io")
    assert view.state == RunState.QUEUED.value
    final = _drive(redis, view.id, tenant)
    assert final == RunState.DONE.value
    status = queries.get_status(tenant, view.id)
    assert status.pct == 100
    assert status.stalled is False


def test_transition_is_atomic_under_concurrency(redis, authorized_session):
    tenant, session_id = authorized_session
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    results: list[str] = []

    def attempt():
        try:
            service.transition(
                redis, tenant_id=tenant, run_id=view.id,
                to_state=RunState.DISCOVERING, stage=RunStage.DISCOVERING,
            )
            results.append("ok")
        except (service.TransitionConflict, sm.InvalidTransition):
            # Two ways to lose the race: the guarded UPDATE matched no row, or
            # by read time the run had already left QUEUED. Both mean "not me".
            results.append("lost")

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # The invariant: exactly one transition is applied, never two.
    assert results.count("ok") == 1
    assert results.count("lost") == 7


def test_tenant_isolation_blocks_cross_tenant_reads(redis, authorized_session):
    tenant_a, session_id = authorized_session
    view = coordinator.start_run(redis, tenant_id=tenant_a, session_id=session_id)
    tenant_b = sessions_service.create_tenant("intruder")

    # Tenant B cannot see Tenant A's run through the read model...
    assert queries.get_status(tenant_b, view.id) is None
    # ...nor via a direct query under B's tenant GUC (RLS at the data layer).
    session = SessionLocal()
    try:
        with session.begin():
            session.execute(text("SET LOCAL app.current_tenant = :t"), {"t": tenant_b})
            count = session.execute(
                text("SELECT count(*) FROM run WHERE id = :rid"), {"rid": view.id}
            ).scalar()
        assert count == 0
    finally:
        session.close()


def test_pause_then_resume_completes(redis, authorized_session):
    tenant, session_id = authorized_session
    view = coordinator.start_run(redis, tenant_id=tenant, session_id=session_id)
    service.request_pause(redis, tenant_id=tenant, run_id=view.id)
    state = _drive(redis, view.id, tenant, max_passes=10)
    assert state == RunState.PAUSED.value

    coordinator.resume_run(redis, tenant_id=tenant, run_id=view.id)
    final = _drive(redis, view.id, tenant)
    assert final == RunState.DONE.value


def test_cancel_stops_the_run(redis, authorized_session):
    tenant, session_id = authorized_session
    view = coordinator.start_run(redis, tenant_id=tenant, session_id=session_id)
    service.request_cancel(redis, tenant_id=tenant, run_id=view.id)
    state = _drive(redis, view.id, tenant, max_passes=10)
    assert state == RunState.CANCELLED.value


def test_duplicate_delivery_is_idempotent(redis, authorized_session):
    tenant, session_id = authorized_session
    view = coordinator.start_run(redis, tenant_id=tenant, session_id=session_id)

    streams.ensure_group(redis, streams.QueueName.DISCOVER)
    batch = streams.read_batch(redis, streams.QueueName.DISCOVER, "w", block_ms=100)
    assert len(batch) == 1
    msg_id, message = batch[0]

    first = worker.process_message(redis, streams.QueueName.DISCOVER, msg_id, dict(message))
    second = worker.process_message(redis, streams.QueueName.DISCOVER, msg_id, dict(message))

    assert first == "done"
    assert second == "duplicate"
    # The single successful pass advanced the run exactly once -> one fetch job.
    assert redis.xlen(streams.queue_key(streams.QueueName.FETCH)) == 1


def test_fatal_failure_dead_letters_and_fails_run(redis, authorized_session, monkeypatch):
    tenant, session_id = authorized_session
    view = coordinator.start_run(redis, tenant_id=tenant, session_id=session_id)

    def boom(*args, **kwargs):
        raise retry.FatalError("unparseable")

    monkeypatch.setattr(progress, "beat", boom)
    final = _drive(redis, view.id, tenant, max_passes=5)

    assert final == RunState.FAILED.value
    dlq = redis.xrange(streams.dlq_key(streams.QueueName.DISCOVER))
    assert len(dlq) >= 1
