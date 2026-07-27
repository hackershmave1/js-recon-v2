import pytest

from recon.db.base import tenant_session
from recon.domain import RunStage, RunState
from recon.events.log import record_event
from recon.runs import assets, coordinator, queries, service

pytestmark = pytest.mark.integration


def _walk_to_correlating(redis, tenant, run_id):
    # Legal DONE/PARTIAL requires the run to actually be at CORRELATING first.
    for st in (RunState.DISCOVERING, RunState.FETCHING, RunState.INGESTING,
               RunState.ANALYZING, RunState.CORRELATING):
        service.transition(redis, tenant_id=tenant, run_id=run_id, to_state=st,
                           stage=RunStage(st.value))


def _crawl_run_at_correlating(redis, tenant, session_id, *, crawl_status, urls):
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id, target="acme.io")
    with tenant_session(tenant) as s:
        assets.seed_pending(s, tenant_id=tenant, run_id=view.id, urls=urls)
        record_event(s, tenant_id=tenant, run_id=view.id, event_type="discover.assets",
                     payload={"count": len(urls), "assets_ref": "x", "status": crawl_status})
    _walk_to_correlating(redis, tenant, view.id)
    return view.id


def _finalize(redis, tenant, run_id):
    coordinator.advance(redis, tenant_id=tenant, run_id=run_id, completed=RunStage.CORRELATING)
    return queries.get_run_flags(tenant, run_id).state


def test_all_ok_is_done(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _crawl_run_at_correlating(redis, tenant, session_id,
                                       crawl_status="ok", urls=["https://acme.io/a.js"])
    aid = assets.list_for_run(tenant, run_id)[0].id
    with tenant_session(tenant) as s:
        assets.set_fetch_ok(s, aid, "k"); assets.set_analyze_ok(s, aid)
    assert _finalize(redis, tenant, run_id) == "done"


def test_one_fetch_fail_is_partial(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _crawl_run_at_correlating(redis, tenant, session_id,
                                       crawl_status="ok", urls=["https://acme.io/a.js"])
    aid = assets.list_for_run(tenant, run_id)[0].id
    with tenant_session(tenant) as s:
        assets.set_fetch_failed(s, aid, "404")
    assert _finalize(redis, tenant, run_id) == "partial"


def test_capped_crawl_is_partial(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _crawl_run_at_correlating(redis, tenant, session_id,
                                       crawl_status="capped", urls=["https://acme.io/a.js"])
    aid = assets.list_for_run(tenant, run_id)[0].id
    with tenant_session(tenant) as s:
        assets.set_fetch_ok(s, aid, "k"); assets.set_analyze_ok(s, aid)
    assert _finalize(redis, tenant, run_id) == "partial"


def test_zero_asset_timeout_crawl_is_partial(redis, authorized_session):
    # A crawl that timed out before finding anything has zero run_asset rows --
    # the discriminator for "is this a crawl run" is the discover.assets event
    # itself, NOT the row count, so this must still finalize PARTIAL rather than
    # falling through to the legacy hardcoded DONE.
    tenant, session_id = authorized_session
    run_id = _crawl_run_at_correlating(redis, tenant, session_id,
                                       crawl_status="timeout", urls=[])
    assert _finalize(redis, tenant, run_id) == "partial"


def test_zero_asset_ok_crawl_is_done(redis, authorized_session):
    # A clean crawl that legitimately found zero in-scope assets: `all()` over an
    # empty run_asset set is vacuously True, so fetch_ok/analyze_ok both hold and
    # the run finalizes DONE.
    tenant, session_id = authorized_session
    run_id = _crawl_run_at_correlating(redis, tenant, session_id,
                                       crawl_status="ok", urls=[])
    assert _finalize(redis, tenant, run_id) == "done"


def test_legacy_run_without_discover_assets_event_is_done(redis, authorized_session):
    # A pre-crawl (upload/single-URL) run never records a discover.assets event at
    # all -- that absence, not an empty run_asset list, is what routes to the
    # legacy hardcoded DONE.
    tenant, session_id = authorized_session
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id, target="acme.io")
    _walk_to_correlating(redis, tenant, view.id)
    assert _finalize(redis, tenant, view.id) == "done"
