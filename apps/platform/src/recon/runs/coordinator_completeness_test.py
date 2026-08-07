import pytest

from recon.db.base import tenant_session
from recon.domain import RunStage, RunState
from recon.events.log import record_event
from recon.runs import assets, coordinator, queries, service

pytestmark = pytest.mark.integration


def _walk_to_correlating(redis, tenant, run_id):
    # Legal DONE/PARTIAL requires the run to actually be at CORRELATING first.
    for st in (
        RunState.DISCOVERING,
        RunState.FETCHING,
        RunState.INGESTING,
        RunState.ANALYZING,
        RunState.CORRELATING,
    ):
        service.transition(
            redis, tenant_id=tenant, run_id=run_id, to_state=st, stage=RunStage(st.value)
        )


def _crawl_run_at_correlating(redis, tenant, session_id, *, crawl_status, urls):
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id, target="acme.io")
    with tenant_session(tenant) as s:
        assets.seed_pending(s, tenant_id=tenant, run_id=view.id, urls=urls)
        record_event(
            s,
            tenant_id=tenant,
            run_id=view.id,
            event_type="discover.assets",
            payload={"count": len(urls), "assets_ref": "x", "status": crawl_status},
        )
    _walk_to_correlating(redis, tenant, view.id)
    return view.id


def _finalize(redis, tenant, run_id):
    coordinator.advance(redis, tenant_id=tenant, run_id=run_id, completed=RunStage.CORRELATING)
    return queries.get_run_flags(tenant, run_id).state


def test_all_ok_is_done(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _crawl_run_at_correlating(
        redis, tenant, session_id, crawl_status="ok", urls=["https://acme.io/a.js"]
    )
    aid = assets.list_for_run(tenant, run_id)[0].id
    with tenant_session(tenant) as s:
        assets.set_fetch_ok(s, aid, "k")
        assets.set_analyze_ok(s, aid)
    assert _finalize(redis, tenant, run_id) == "done"


def test_one_fetch_fail_is_partial(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _crawl_run_at_correlating(
        redis, tenant, session_id, crawl_status="ok", urls=["https://acme.io/a.js"]
    )
    aid = assets.list_for_run(tenant, run_id)[0].id
    with tenant_session(tenant) as s:
        assets.set_fetch_failed(s, aid, "404")
    assert _finalize(redis, tenant, run_id) == "partial"


def test_finalize_keeps_last_stage_for_progress_ui(redis, authorized_session):
    # A terminal transition must KEEP run.stage = the last active stage (here
    # CORRELATING), not null it: the run-progress stepper reads it via get_status
    # to render "stopped in <stage>" for a partial/failed/cancelled run instead of
    # a blank pipeline. Regression for the null-stage-at-terminal fix.
    tenant, session_id = authorized_session
    run_id = _crawl_run_at_correlating(
        redis, tenant, session_id, crawl_status="ok", urls=["https://acme.io/a.js"]
    )
    aid = assets.list_for_run(tenant, run_id)[0].id
    with tenant_session(tenant) as s:
        assets.set_fetch_failed(s, aid, "404")
    assert _finalize(redis, tenant, run_id) == "partial"
    assert queries.get_status(tenant, run_id).stage == RunStage.CORRELATING.value


def test_capped_crawl_is_partial(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _crawl_run_at_correlating(
        redis, tenant, session_id, crawl_status="capped", urls=["https://acme.io/a.js"]
    )
    aid = assets.list_for_run(tenant, run_id)[0].id
    with tenant_session(tenant) as s:
        assets.set_fetch_ok(s, aid, "k")
        assets.set_analyze_ok(s, aid)
    assert _finalize(redis, tenant, run_id) == "partial"


def test_zero_asset_timeout_crawl_is_partial(redis, authorized_session):
    # A crawl that timed out before finding anything has zero run_asset rows --
    # the discriminator for "is this a crawl run" is the discover.assets event
    # itself, NOT the row count, so this must still finalize PARTIAL rather than
    # falling through to the legacy hardcoded DONE.
    tenant, session_id = authorized_session
    run_id = _crawl_run_at_correlating(redis, tenant, session_id, crawl_status="timeout", urls=[])
    assert _finalize(redis, tenant, run_id) == "partial"


def test_zero_asset_ok_crawl_is_done(redis, authorized_session):
    # A clean crawl that legitimately found zero in-scope assets: `all()` over an
    # empty run_asset set is vacuously True, so fetch_ok/analyze_ok both hold and
    # the run finalizes DONE.
    tenant, session_id = authorized_session
    run_id = _crawl_run_at_correlating(redis, tenant, session_id, crawl_status="ok", urls=[])
    assert _finalize(redis, tenant, run_id) == "done"


def test_legacy_run_without_discover_assets_event_is_done(redis, authorized_session):
    # A pre-crawl (upload/single-URL) run never records a discover.assets event at
    # all -- that absence, not an empty run_asset list, is what routes to the
    # legacy hardcoded DONE.
    tenant, session_id = authorized_session
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id, target="acme.io")
    _walk_to_correlating(redis, tenant, view.id)
    assert _finalize(redis, tenant, view.id) == "done"


def _run_ready_to_finalize(redis, tenant, session_id):
    # A plain (legacy, non-crawl) run walked to CORRELATING -- ready for advance
    # to finalize. The reclassify hook doesn't care about DONE vs PARTIAL, only
    # that finalize completes, so the simplest run shape is enough here.
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id, target="acme.io")
    _walk_to_correlating(redis, tenant, view.id)
    return view.id


def test_advance_reclassifies_when_session_spec_present(monkeypatch, redis, authorized_session):
    # Task 11 (REQ-D5 gate N3): a fresh finalize triggers reclassification
    # against the session's already-attached spec. reclassify_run itself is a
    # no-op without a spec (Task 8), so recording the call args is enough to
    # prove the hook fires -- no need to seed a real SessionSpec here.
    tenant, session_id = authorized_session
    run_id = _run_ready_to_finalize(redis, tenant, session_id)
    called = {}
    monkeypatch.setattr(
        "recon.spec.service.reclassify_run",
        lambda t, r: called.setdefault("hit", (t, r)),
    )
    assert _finalize(redis, tenant, run_id) == "done"
    assert called["hit"] == (tenant, run_id)


def test_advance_no_reclassify_without_spec(redis, authorized_session):
    # No SessionSpec exists for this session, so the real reclassify_run takes
    # its no-op path (returns None) -- finalize must proceed exactly as before,
    # unaffected by the new hook.
    tenant, session_id = authorized_session
    run_id = _run_ready_to_finalize(redis, tenant, session_id)
    assert _finalize(redis, tenant, run_id) == "done"


def test_advance_reclassify_failure_does_not_break_finalize(monkeypatch, redis, authorized_session):
    # Classification is best-effort: even if reclassify_run blows up, advance()
    # must swallow it and let the run reach its terminal state rather than the
    # exception surfacing and failing the whole finalize (and thus the job).
    tenant, session_id = authorized_session
    run_id = _run_ready_to_finalize(redis, tenant, session_id)

    def _boom(_tenant_id, _run_id):
        raise RuntimeError("classification exploded")

    monkeypatch.setattr("recon.spec.service.reclassify_run", _boom)
    assert _finalize(redis, tenant, run_id) == "done"


def test_advance_does_not_reclassify_on_duplicate_finalize(monkeypatch, redis, authorized_session):
    # The reclassify hook must sit on the fresh-finalize path only. A second
    # advance() for the same completed stage takes the TransitionConflict/
    # idempotent branch, which must not call reclassify_run again.
    tenant, session_id = authorized_session
    run_id = _run_ready_to_finalize(redis, tenant, session_id)
    calls = []
    monkeypatch.setattr(
        "recon.spec.service.reclassify_run",
        lambda t, r: calls.append((t, r)),
    )
    assert _finalize(redis, tenant, run_id) == "done"
    coordinator.advance(redis, tenant_id=tenant, run_id=run_id, completed=RunStage.CORRELATING)
    assert calls == [(tenant, run_id)]
