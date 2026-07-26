import pytest

from recon.db import models
from recon.db.base import tenant_session
from recon.runs import assets
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


def _run(tenant, session_id):
    with tenant_session(tenant) as s:
        run = models.Run(tenant_id=tenant, session_id=session_id)
        s.add(run)
        s.flush()
        return str(run.id)


def test_seed_is_idempotent_and_listable(authorized_session):
    tenant, session_id = authorized_session
    run_id = _run(tenant, session_id)
    urls = ["https://acme.io/a.js", "https://acme.io/b.js"]
    with tenant_session(tenant) as s:
        assets.seed_pending(s, tenant_id=tenant, run_id=run_id, urls=urls)
    with tenant_session(tenant) as s:  # re-seed (redelivery) adds nothing
        assets.seed_pending(s, tenant_id=tenant, run_id=run_id, urls=urls)
    rows = assets.list_for_run(tenant, run_id)
    assert [r.url for r in rows] == urls
    assert all(r.fetch_status == "pending" for r in rows)


def test_status_setters(authorized_session):
    tenant, session_id = authorized_session
    run_id = _run(tenant, session_id)
    with tenant_session(tenant) as s:
        assets.seed_pending(s, tenant_id=tenant, run_id=run_id, urls=["https://acme.io/a.js"])
    asset_id = assets.list_for_run(tenant, run_id)[0].id
    with tenant_session(tenant) as s:
        assets.set_fetch_ok(s, asset_id, "t/r/input/deadbeef")
    with tenant_session(tenant) as s:
        assets.set_analyze_failed(s, asset_id, "boom")
    row = assets.list_for_run(tenant, run_id)[0]
    assert row.input_ref == "t/r/input/deadbeef"
    assert row.fetch_status == "ok" and row.analyze_status == "failed"
