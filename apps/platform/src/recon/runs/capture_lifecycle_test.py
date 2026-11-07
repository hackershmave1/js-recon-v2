"""Capture lifecycle (Start → Analyze now → late files → End): one capture is one run, Analyze
never ends it, late files get another pass, End finishes it. Integration: real Postgres/Redis/S3."""

import json

import pytest
from sqlalchemy import func, select, text, update

from recon import storage
from recon.db.base import tenant_session
from recon.db.models import Job, Run
from recon.discover import manifest as discovery
from recon.domain import RunStage, RunState
from recon.runs import assets, capture_lifecycle, service

pytestmark = pytest.mark.integration

EXT = "ext-session-1"


def _run(tenant, run_id):
    with tenant_session(tenant) as s:
        return s.scalar(select(Run).where(Run.id == run_id))


def _jobs(tenant, run_id, stage=RunStage.DISCOVERING):
    with tenant_session(tenant) as s:
        return s.scalar(
            select(func.count(Job.id)).where(Job.run_id == run_id, Job.stage == stage.value)
        )


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


def _to_capturing(redis, tenant, run_id):
    for st in (
        RunState.DISCOVERING,
        RunState.FETCHING,
        RunState.INGESTING,
        RunState.ANALYZING,
        RunState.CORRELATING,
        RunState.CAPTURING,
    ):
        service.transition(
            redis,
            tenant_id=tenant,
            run_id=run_id,
            to_state=st,
            stage=RunStage(st.value) if st != RunState.CAPTURING else None,
        )


def test_start_opens_a_capture_and_ends_one_left_open(redis, authorized_session):
    tenant, session_id = authorized_session
    first = capture_lifecycle.start_capture(redis, tenant, session_id, EXT)
    _add_files(tenant, first, 1)

    second = capture_lifecycle.start_capture(redis, tenant, session_id, EXT)

    assert second != first
    assert _run(tenant, second).capture_external_id == EXT
    assert _run(tenant, first).capture_external_id is None  # ended
    assert _jobs(tenant, first) == 1  # ...with its final analysis queued


def test_analyze_keeps_the_capture_open_and_starts_one_pass(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = capture_lifecycle.start_capture(redis, tenant, session_id, EXT)
    _add_files(tenant, run_id, 2)

    first = capture_lifecycle.analyze(redis, tenant, run_id, observations=[])
    again = capture_lifecycle.analyze(redis, tenant, run_id, observations=[])

    assert first["started"] is True
    assert again["started"] is True  # idempotent for the caller...
    assert _jobs(tenant, run_id) == 1  # ...but only one pass
    assert _run(tenant, run_id).capture_external_id == EXT  # still open


def test_analyze_with_no_files_starts_nothing(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = capture_lifecycle.start_capture(redis, tenant, session_id, EXT)

    result = capture_lifecycle.analyze(redis, tenant, run_id, observations=[])

    assert result["started"] is False
    assert _jobs(tenant, run_id) == 0


def test_analyze_on_a_resting_capture_starts_the_next_pass(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = capture_lifecycle.start_capture(redis, tenant, session_id, EXT)
    _add_files(tenant, run_id, 1, analyzed=True)
    capture_lifecycle.analyze(redis, tenant, run_id, observations=[])
    _to_capturing(redis, tenant, run_id)
    _add_files(tenant, run_id, 1, start=1)

    result = capture_lifecycle.analyze(redis, tenant, run_id, observations=[])

    assert result["started"] is True
    assert _jobs(tenant, run_id) == 2


def test_a_batch_after_the_first_analysis_schedules_a_pass(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = capture_lifecycle.start_capture(redis, tenant, session_id, EXT)
    with tenant_session(tenant) as s:
        capture_lifecycle.note_batch(s, run_id)
    assert _run(tenant, run_id).reanalyze_after is None  # never analyzed: nothing to update

    _add_files(tenant, run_id, 1)
    capture_lifecycle.analyze(redis, tenant, run_id, observations=[])
    with tenant_session(tenant) as s:
        capture_lifecycle.note_batch(s, run_id)
        due_in = s.scalar(
            select(Run.reanalyze_after - func.now()).where(Run.id == run_id)
        ).total_seconds()

    assert 8 < due_in <= capture_lifecycle.QUIET_SECONDS


def test_end_on_a_resting_capture_with_nothing_new_finishes_it(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = capture_lifecycle.start_capture(redis, tenant, session_id, EXT)
    _add_files(tenant, run_id, 1, analyzed=True)
    capture_lifecycle.analyze(redis, tenant, run_id, observations=[])
    _to_capturing(redis, tenant, run_id)

    capture_lifecycle.end_capture(redis, tenant, run_id, observations=[])

    run = _run(tenant, run_id)
    assert run.state == RunState.DONE.value
    assert run.capture_external_id is None
    assert run.ended_at is not None


def test_end_with_unanalyzed_files_runs_a_final_pass(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = capture_lifecycle.start_capture(redis, tenant, session_id, EXT)
    _add_files(tenant, run_id, 1, analyzed=True)
    capture_lifecycle.analyze(redis, tenant, run_id, observations=[])
    _to_capturing(redis, tenant, run_id)
    _add_files(tenant, run_id, 1, start=1)

    capture_lifecycle.end_capture(redis, tenant, run_id, observations=[])

    run = _run(tenant, run_id)
    assert run.state == RunState.CAPTURING.value  # the final pass decides DONE/PARTIAL
    assert run.capture_external_id is None
    assert _jobs(tenant, run_id) == 2


def test_end_before_any_analysis_queues_the_analysis(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = capture_lifecycle.start_capture(redis, tenant, session_id, EXT)
    _add_files(tenant, run_id, 2)

    capture_lifecycle.end_capture(redis, tenant, run_id, observations=[])

    assert _run(tenant, run_id).capture_external_id is None
    assert _jobs(tenant, run_id) == 1


def test_ending_an_empty_capture_leaves_nothing_behind(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = capture_lifecycle.start_capture(redis, tenant, session_id, EXT)

    capture_lifecycle.end_capture(redis, tenant, run_id, observations=[])

    assert _run(tenant, run_id) is None


def test_end_is_idempotent(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = capture_lifecycle.start_capture(redis, tenant, session_id, EXT)
    _add_files(tenant, run_id, 1)

    capture_lifecycle.end_capture(redis, tenant, run_id, observations=[])
    capture_lifecycle.end_capture(redis, tenant, run_id, observations=[])

    assert _jobs(tenant, run_id) == 1


def test_observations_are_merged_across_calls(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = capture_lifecycle.start_capture(redis, tenant, session_id, EXT)
    _add_files(tenant, run_id, 1)
    a = {"method": "GET", "url": "https://api.acme.io/a"}
    b = {"method": "POST", "url": "https://api.acme.io/b"}

    capture_lifecycle.analyze(redis, tenant, run_id, observations=[a])
    capture_lifecycle.end_capture(redis, tenant, run_id, observations=[b, a])

    found = discovery.read_latest(tenant, run_id)
    assert found is not None and found.requests_ref
    assert json.loads(storage.get_blob(found.requests_ref)) == [a, b]


def test_sweeper_starts_due_passes_only(redis, authorized_session):
    tenant, session_id = authorized_session
    due = capture_lifecycle.start_capture(redis, tenant, session_id, EXT)
    _add_files(tenant, due, 1, analyzed=True)
    capture_lifecycle.analyze(redis, tenant, due, observations=[])
    _to_capturing(redis, tenant, due)
    _add_files(tenant, due, 1, start=1)
    with tenant_session(tenant) as s:
        s.execute(
            update(Run)
            .where(Run.id == due)
            .values(reanalyze_after=func.now() - text("interval '1 second'"))
        )

    capture_lifecycle.sweep(redis)
    capture_lifecycle.sweep(redis)  # a second tick must not start a second pass

    assert _jobs(tenant, due) == 2
    assert _run(tenant, due).reanalyze_after is None


def test_sweeper_ends_a_capture_idle_for_a_day(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = capture_lifecycle.start_capture(redis, tenant, session_id, EXT)
    _add_files(tenant, run_id, 1)
    with tenant_session(tenant) as s:
        s.execute(
            text("UPDATE run_asset SET created_at = now() - interval '25 hours' WHERE run_id = :r"),
            {"r": run_id},
        )

    capture_lifecycle.sweep(redis)

    assert _run(tenant, run_id).capture_external_id is None
    assert _jobs(tenant, run_id) == 1


def test_sweeper_leaves_an_active_capture_open(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = capture_lifecycle.start_capture(redis, tenant, session_id, EXT)
    _add_files(tenant, run_id, 1)

    capture_lifecycle.sweep(redis)

    assert _run(tenant, run_id).capture_external_id == EXT


def test_cancelling_a_resting_capture_is_immediate_and_closes_it(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = capture_lifecycle.start_capture(redis, tenant, session_id, EXT)
    _add_files(tenant, run_id, 1, analyzed=True)
    capture_lifecycle.analyze(redis, tenant, run_id, observations=[])
    _to_capturing(redis, tenant, run_id)

    service.request_cancel(redis, tenant_id=tenant, run_id=run_id)

    run = _run(tenant, run_id)
    assert run.state == RunState.CANCELLED.value
    assert run.capture_external_id is None  # a cancelled capture takes no more files


def test_the_popup_summary_reads_a_resting_capture(redis, authorized_session):
    from recon.findings import queries as findings_queries

    tenant, session_id = authorized_session
    run_id = capture_lifecycle.start_capture(redis, tenant, session_id, EXT)
    _add_files(tenant, run_id, 1, analyzed=True)
    capture_lifecycle.analyze(redis, tenant, run_id, observations=[])
    _to_capturing(redis, tenant, run_id)

    summary = findings_queries.get_session_findings_summary(tenant, session_id)

    assert summary is not None
