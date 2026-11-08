"""Finalize for the capture lifecycle: a pass over an OPEN capture rests in CAPTURING instead of
ending the run, and files that arrived mid-pass get another pass. Terminal stays final."""

import pytest
from sqlalchemy import select

from recon.db.base import tenant_session
from recon.db.models import Run
from recon.domain import RunStage, RunState
from recon.events.log import record_event
from recon.runs import assets, coordinator, queries, service

pytestmark = pytest.mark.integration


def _capture_run_at_correlating(redis, tenant, session_id, *, marker, analyzed, pending=0):
    view = service.create_run(
        redis, tenant_id=tenant, session_id=session_id, capture_external_id=marker
    )
    urls = [f"https://app.acme.io/{i}.js" for i in range(analyzed + pending)]
    with tenant_session(tenant) as s:
        assets.seed_pending(s, tenant_id=tenant, run_id=view.id, urls=urls)
        record_event(
            s,
            tenant_id=tenant,
            run_id=view.id,
            event_type="discover.assets",
            payload={"count": len(urls), "assets_ref": "x", "status": "ok"},
        )
    for i, row in enumerate(assets.list_for_run(tenant, view.id)):
        with tenant_session(tenant) as s:
            assets.set_fetch_ok(s, row.id, f"k{i}")
            if i < analyzed:
                assets.set_analyze_ok(s, row.id)
    for st in (
        RunState.DISCOVERING,
        RunState.FETCHING,
        RunState.INGESTING,
        RunState.ANALYZING,
        RunState.CORRELATING,
    ):
        service.transition(
            redis, tenant_id=tenant, run_id=view.id, to_state=st, stage=RunStage(st.value)
        )
    return view.id


def _finalize(redis, tenant, run_id):
    coordinator.advance(redis, tenant_id=tenant, run_id=run_id, completed=RunStage.CORRELATING)
    with tenant_session(tenant) as s:
        run = s.scalar(select(Run).where(Run.id == run_id))
        return run.state, run.reanalyze_after, run.ended_at


def test_open_capture_rests_in_capturing_after_a_pass(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _capture_run_at_correlating(redis, tenant, session_id, marker="ext-1", analyzed=2)

    state, reanalyze_after, ended_at = _finalize(redis, tenant, run_id)

    assert state == RunState.CAPTURING.value
    assert reanalyze_after is None  # nothing new to analyze
    assert ended_at is None  # not finished: no terminal timestamp
    assert queries.get_status(tenant, run_id).stage == RunStage.CORRELATING.value


def test_files_that_arrived_mid_pass_get_another_pass(redis, authorized_session):
    # Ended (marker NULL) while the final pass ran, but 1 file landed during it: the run must not
    # finish PARTIAL with an unanalyzed file — it rests and is due for one more pass right away.
    tenant, session_id = authorized_session
    run_id = _capture_run_at_correlating(
        redis, tenant, session_id, marker=None, analyzed=2, pending=1
    )

    state, reanalyze_after, ended_at = _finalize(redis, tenant, run_id)

    assert state == RunState.CAPTURING.value
    assert reanalyze_after is not None
    assert ended_at is None


def test_ended_capture_with_everything_analyzed_is_done(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _capture_run_at_correlating(redis, tenant, session_id, marker=None, analyzed=2)

    state, reanalyze_after, ended_at = _finalize(redis, tenant, run_id)

    assert state == RunState.DONE.value
    assert reanalyze_after is None
    assert ended_at is not None
