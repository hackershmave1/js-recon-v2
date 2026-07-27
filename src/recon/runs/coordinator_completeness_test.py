import pytest

from recon.db.base import tenant_session
from recon.domain import RunStage, RunState
from recon.events.log import record_event
from recon.runs import assets, coordinator, queries, service

pytestmark = pytest.mark.integration


def _crawl_run_at_correlating(redis, tenant, session_id, *, crawl_status, urls):
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id, target="acme.io")
    with tenant_session(tenant) as s:
        assets.seed_pending(s, tenant_id=tenant, run_id=view.id, urls=urls)
        record_event(s, tenant_id=tenant, run_id=view.id, event_type="discover.assets",
                     payload={"count": len(urls), "assets_ref": "x", "status": crawl_status})
    # walk to CORRELATING so DONE/PARTIAL is legal
    for st in (RunState.DISCOVERING, RunState.FETCHING, RunState.INGESTING,
               RunState.ANALYZING, RunState.CORRELATING):
        service.transition(redis, tenant_id=tenant, run_id=view.id, to_state=st,
                           stage=RunStage(st.value))
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
