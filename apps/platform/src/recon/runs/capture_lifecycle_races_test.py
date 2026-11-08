"""Capture lifecycle under failure and concurrency: the code-review probes, turned into
regressions. Each test is a path that used to strand a capture or wedge its session."""

import pytest
from sqlalchemy import func, select, text, update

from recon.db.base import tenant_session
from recon.db.models import Job, Run
from recon.domain import QueueName, RunStage, RunState
from recon.runs import assets, capture_lifecycle, capture_pass, capture_sweep, coordinator, service

pytestmark = pytest.mark.integration

EXT = "ext-races"
_PASS = [
    RunState.DISCOVERING,
    RunState.FETCHING,
    RunState.INGESTING,
    RunState.ANALYZING,
    RunState.CORRELATING,
]


def _run(tenant, run_id):
    with tenant_session(tenant) as s:
        return s.scalar(select(Run).where(Run.id == run_id))


def _add_files(tenant, run_id, n, *, start=0, analyzed=False):
    urls = [f"https://app.acme.io/{i}.js" for i in range(start, start + n)]
    with tenant_session(tenant) as s:
        assets.seed_pending(s, tenant_id=tenant, run_id=run_id, urls=urls)
    for row in assets.list_for_run(tenant, run_id):
        if row.url in urls:
            with tenant_session(tenant) as s:
                assets.set_fetch_ok(s, row.id, f"k-{row.url}")
                if analyzed:
                    assets.set_analyze_ok(s, row.id)


def _walk(redis, tenant, run_id, states):
    for st in states:
        service.transition(
            redis,
            tenant_id=tenant,
            run_id=run_id,
            to_state=st,
            stage=RunStage(st.value) if st.value in {s.value for s in RunStage} else None,
        )


def _resting(redis, tenant, session_id):
    """An open capture with one analyzed file, resting after its first pass."""
    run_id = capture_lifecycle.start_capture(redis, tenant, session_id, EXT)
    _add_files(tenant, run_id, 1, analyzed=True)
    capture_lifecycle.analyze(redis, tenant, run_id, observations=[])
    _finish_live_jobs(tenant, run_id)
    _walk(redis, tenant, run_id, [*_PASS, RunState.CAPTURING])
    return run_id


def _finish_live_jobs(tenant, run_id):
    with tenant_session(tenant) as s:
        s.execute(
            update(Job)
            .where(Job.run_id == run_id, Job.state.in_(["queued", "running"]))
            .values(state="succeeded")
        )


def _passes(tenant, run_id):
    with tenant_session(tenant) as s:
        return s.scalar(
            select(func.count(Job.id)).where(
                Job.run_id == run_id, Job.stage == RunStage.DISCOVERING.value
            )
        )


def test_a_failed_pass_releases_the_capture(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = capture_lifecycle.start_capture(redis, tenant, session_id, EXT)
    _add_files(tenant, run_id, 1)
    capture_lifecycle.analyze(redis, tenant, run_id, observations=[])

    _walk(redis, tenant, run_id, [RunState.DISCOVERING, RunState.FAILED])

    assert _run(tenant, run_id).capture_external_id is None
    assert capture_lifecycle.find_open_run(tenant, EXT) is None
    assert capture_lifecycle.start_capture(redis, tenant, session_id, EXT) != run_id


def test_a_queued_pass_is_not_queued_twice(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _resting(redis, tenant, session_id)
    _add_files(tenant, run_id, 1, start=5)
    before = _passes(tenant, run_id)

    capture_lifecycle.analyze(redis, tenant, run_id, observations=[])
    capture_lifecycle.analyze(redis, tenant, run_id, observations=[])  # worker hasn't started it
    capture_lifecycle.end_capture(redis, tenant, run_id, observations=[])

    assert _passes(tenant, run_id) - before == 1


def test_analyze_with_nothing_new_starts_no_pass(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _resting(redis, tenant, session_id)
    before = _passes(tenant, run_id)

    result = capture_lifecycle.analyze(redis, tenant, run_id, observations=[])

    assert result["started"] is False and result["upToDate"] is True
    assert _passes(tenant, run_id) == before


def test_pausing_a_resting_capture_is_refused(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _resting(redis, tenant, session_id)

    with pytest.raises(service.RunNotPausable):
        service.request_pause(redis, tenant_id=tenant, run_id=run_id)
    assert not _run(tenant, run_id).pause_requested


def test_a_stale_pause_flag_cannot_wedge_a_pass(redis, authorized_session):
    from recon.worker import main as worker

    tenant, session_id = authorized_session
    run_id = _resting(redis, tenant, session_id)
    _add_files(tenant, run_id, 1, start=5)
    capture_lifecycle.analyze(redis, tenant, run_id, observations=[])
    with tenant_session(tenant) as s:  # a pause requested mid-pass, carried into CAPTURING
        s.execute(update(Run).where(Run.id == run_id).values(pause_requested=True))
        job = s.scalars(select(Job).where(Job.run_id == run_id, Job.state == "queued")).one()
        message = {
            "job_id": str(job.id),
            "run_id": run_id,
            "tenant_id": tenant,
            "queue": QueueName.DISCOVER.value,
            "stage": RunStage.DISCOVERING.value,
            "attempts": 0,
            "max_attempts": 3,
        }

    outcome = worker.process_message(redis, QueueName.DISCOVER, "0-1", message)

    assert outcome == "skipped"  # acked, not raised (an unacked message is retried forever)


def test_a_skipped_duplicate_job_is_finished_not_reaped_into_failure(redis, authorized_session):
    from recon.worker import main as worker

    tenant, session_id = authorized_session
    run_id = _resting(redis, tenant, session_id)
    with tenant_session(tenant) as s:
        job_id, message = coordinator.create_stage_job(
            s, tenant_id=tenant, run_id=run_id, stage=RunStage.CORRELATING
        )

    assert worker.process_message(redis, QueueName.ANALYZE, "0-1", message) == "skipped"
    with tenant_session(tenant) as s:
        assert s.get(Job, job_id).state != "running"
    worker.reap_orphaned_jobs(redis)
    assert _run(tenant, run_id).state == RunState.CAPTURING.value


def test_end_observations_get_correlated(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _resting(redis, tenant, session_id)
    before = _passes(tenant, run_id)

    capture_lifecycle.end_capture(
        redis, tenant, run_id, observations=[{"method": "GET", "url": "https://api.acme.io/new"}]
    )

    assert _passes(tenant, run_id) == before + 1  # a final pass so correlate sees them


def test_a_batch_after_end_is_refused_in_its_own_transaction(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _resting(redis, tenant, session_id)
    assert capture_lifecycle.find_open_run(tenant, EXT) == run_id  # the batch passed its gate
    capture_lifecycle.end_capture(redis, tenant, run_id, observations=[])  # End lands first

    with tenant_session(tenant) as s:
        accepted = capture_lifecycle.accept_batch(s, run_id, EXT)

    assert accepted is False


def test_end_racing_finalize_still_finishes(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = capture_lifecycle.start_capture(redis, tenant, session_id, EXT)
    _add_files(tenant, run_id, 1, analyzed=True)
    capture_lifecycle.analyze(redis, tenant, run_id, observations=[])
    _walk(redis, tenant, run_id, _PASS)
    to_state, completeness, _ = coordinator._finalize_state(tenant, run_id)  # reads "open"
    capture_lifecycle.end_capture(redis, tenant, run_id, observations=[])  # End commits here
    service.transition(  # ...then finalize's transition lands
        redis,
        tenant_id=tenant,
        run_id=run_id,
        to_state=to_state,
        extra_values={"completeness": completeness},
    )
    _finish_live_jobs(tenant, run_id)

    capture_sweep.sweep(
        redis
    )  # End asked during the pass: one more pass, never "capturing" forever
    assert _passes(tenant, run_id) == 2
    _finish_live_jobs(tenant, run_id)
    _walk(redis, tenant, run_id, _PASS)
    coordinator.advance(redis, tenant_id=tenant, run_id=run_id, completed=RunStage.CORRELATING)

    assert _run(tenant, run_id).state == RunState.DONE.value


def test_a_sealed_resting_capture_is_finished_by_the_sweeper(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _resting(redis, tenant, session_id)
    with tenant_session(tenant) as s:  # however it got here: sealed, resting, nothing scheduled
        s.execute(update(Run).where(Run.id == run_id).values(capture_external_id=None))

    capture_sweep.sweep(redis)

    assert _run(tenant, run_id).state == RunState.DONE.value


def test_one_broken_run_does_not_stop_the_sweep(redis, authorized_session, monkeypatch):
    tenant, session_id = authorized_session
    broken = _resting(redis, tenant, session_id)
    with tenant_session(tenant) as s:
        s.execute(update(Run).where(Run.id == broken).values(capture_external_id=None))
    real = capture_sweep._settle_resting

    def boom(redis_, tenant_id, run_id):
        if run_id == broken:
            raise RuntimeError("blob store reset")
        return real(redis_, tenant_id, run_id)

    monkeypatch.setattr(capture_sweep, "_settle_resting", boom)

    capture_sweep.sweep(redis)  # must not raise


def test_starting_again_reuses_an_empty_open_capture(redis, authorized_session):
    tenant, session_id = authorized_session
    first = capture_lifecycle.start_capture(redis, tenant, session_id, EXT)

    second = capture_lifecycle.start_capture(redis, tenant, session_id, EXT)

    assert second == first  # not deleted out from under a concurrent Start
    assert _run(tenant, first) is not None


def test_merged_observations_are_capped(redis, authorized_session, monkeypatch):
    monkeypatch.setenv("RECON_CAPTURE_MAX_REQUESTS", "3")
    from recon.config import get_settings

    get_settings.cache_clear()
    try:
        merged = capture_pass.merged_observations(
            None, [{"method": "GET", "url": f"https://api.acme.io/{i}"} for i in range(10)]
        )
    finally:
        get_settings.cache_clear()

    assert len(merged[0]) == 3


def test_an_idle_open_capture_is_found_without_new_files(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = capture_lifecycle.start_capture(redis, tenant, session_id, EXT)
    with tenant_session(tenant) as s:
        s.execute(
            text("UPDATE run SET created_at = now() - interval '25 hours' WHERE id = :r"),
            {"r": run_id},
        )

    capture_sweep.sweep(redis)

    assert _run(tenant, run_id) is None  # empty and idle: ended, nothing left behind


# --- second review: the "one live pass" rule must never be held by a dead job --------------


def _queued_job_message(tenant, run_id):
    with tenant_session(tenant) as s:
        job = s.scalars(select(Job).where(Job.run_id == run_id, Job.state == "queued")).one()
        return job.id, {
            "job_id": str(job.id),
            "run_id": run_id,
            "tenant_id": tenant,
            "queue": job.queue,
            "stage": job.stage,
            "attempts": 0,
            "max_attempts": 3,
        }


def test_pause_and_resume_mid_pass_leave_no_stale_live_job(redis, authorized_session):
    from recon.worker import main as worker

    tenant, session_id = authorized_session
    run_id = capture_lifecycle.start_capture(redis, tenant, session_id, EXT)
    _add_files(tenant, run_id, 1)
    capture_lifecycle.analyze(redis, tenant, run_id, observations=[])
    service.request_pause(redis, tenant_id=tenant, run_id=run_id)  # QUEUED → PAUSED at once
    stale_id, message = _queued_job_message(tenant, run_id)

    assert worker.process_message(redis, QueueName.DISCOVER, "0-1", message) == "paused"
    coordinator.resume_run(redis, tenant_id=tenant, run_id=run_id)

    with tenant_session(tenant) as s:
        assert s.get(Job, stale_id).state == "cancelled"
        live = s.scalar(
            select(func.count(Job.id)).where(
                Job.run_id == run_id, Job.state.in_(capture_pass.LIVE_JOB_STATES)
            )
        )
    assert live == 1  # only the job resume enqueued


def test_a_queued_job_never_delivered_stops_counting_as_live(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _resting(redis, tenant, session_id)
    _add_files(tenant, run_id, 1, start=5)
    capture_lifecycle.analyze(redis, tenant, run_id, observations=[])  # its publish is "lost"
    with tenant_session(tenant) as s:
        s.execute(
            update(Job)
            .where(Job.run_id == run_id, Job.state == "queued")
            .values(created_at=func.now() - text("interval '2 hours'"))
        )
        assert not capture_pass.live_job_exists(s, run_id)


def test_end_while_a_batch_is_committing_still_analyzes_it(redis, authorized_session, monkeypatch):
    tenant, session_id = authorized_session
    run_id = capture_lifecycle.start_capture(redis, tenant, session_id, EXT)
    real = capture_pass.prepare
    calls = []

    def late(tenant_id, rid, obs):
        out = real(tenant_id, rid, obs)
        if not calls:
            _add_files(tenant, run_id, 2)  # the in-flight batch commits after End's prepare()
        calls.append(out)
        return out

    monkeypatch.setattr(capture_pass, "prepare", late)

    capture_lifecycle.end_capture(redis, tenant, run_id, observations=[])

    assert _run(tenant, run_id).capture_external_id is None
    assert _passes(tenant, run_id) == 1  # the final pass covers the batch


def test_the_sweeper_does_not_double_a_pass_end_just_queued(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _resting(redis, tenant, session_id)
    _add_files(tenant, run_id, 1, start=5)
    capture_lifecycle.end_capture(redis, tenant, run_id, observations=[])  # queues the final pass
    with tenant_session(tenant) as s:
        s.execute(update(Run).where(Run.id == run_id).values(reanalyze_after=func.now()))

    assert capture_sweep._settle_resting(redis, tenant, run_id) == 0
    assert _passes(tenant, run_id) == 2  # the first pass + End's, nothing more


def test_a_cancel_during_the_last_stage_cancels(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = capture_lifecycle.start_capture(redis, tenant, session_id, EXT)
    _add_files(tenant, run_id, 1, analyzed=True)
    capture_lifecycle.analyze(redis, tenant, run_id, observations=[])
    _finish_live_jobs(tenant, run_id)
    _walk(redis, tenant, run_id, _PASS)
    service.request_cancel(redis, tenant_id=tenant, run_id=run_id)  # CORRELATING: a flag only

    coordinator.advance(redis, tenant_id=tenant, run_id=run_id, completed=RunStage.CORRELATING)

    run = _run(tenant, run_id)
    assert run.state == RunState.CANCELLED.value
    assert run.capture_external_id is None


def test_end_during_a_running_pass_gets_its_observations_correlated(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = capture_lifecycle.start_capture(redis, tenant, session_id, EXT)
    _add_files(tenant, run_id, 1, analyzed=True)
    capture_lifecycle.analyze(redis, tenant, run_id, observations=[])
    _walk(redis, tenant, run_id, _PASS)  # the pass is in CORRELATING, past its manifest read
    capture_lifecycle.end_capture(
        redis, tenant, run_id, observations=[{"method": "GET", "url": "https://api.acme.io/late"}]
    )
    _finish_live_jobs(tenant, run_id)

    coordinator.advance(redis, tenant_id=tenant, run_id=run_id, completed=RunStage.CORRELATING)
    capture_sweep.sweep(redis)

    assert _run(tenant, run_id).state == RunState.CAPTURING.value
    assert _passes(tenant, run_id) == 2  # one more pass, so correlate reads the observations
