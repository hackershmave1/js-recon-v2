import pytest
from sqlalchemy import select

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.findings import wrapper_service
from recon.findings.wrappers import InvalidWrapperCallee
from recon.runs import service

pytestmark = pytest.mark.integration


def _run(tenant, session_id):
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        return str(run.id)


def _run_with_source(redis, tenant, session_id, source: bytes) -> str:
    from sqlalchemy import update
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    key = storage.put_blob(tenant, view.id, "input", source)
    with tenant_session(tenant) as session:
        session.execute(update(models.Run).where(models.Run.id == view.id).values(input_ref=key))
    return view.id


def test_add_list_delete_wrapper_rule(authorized_session):
    tenant, session_id = authorized_session
    run_id = _run(tenant, session_id)

    res = wrapper_service.add_rule(tenant, run_id, callee="api")
    assert res["rule"]["callee"] == "api"

    rules = wrapper_service.list_rules(tenant, run_id)
    assert len(rules) == 1 and rules[0]["callee"] == "api"

    assert wrapper_service.delete_rule(tenant, run_id, res["rule"]["id"]) is True
    assert wrapper_service.list_rules(tenant, run_id) == []


def test_add_wrapper_upserts_on_callee(authorized_session):
    tenant, session_id = authorized_session
    run_id = _run(tenant, session_id)
    wrapper_service.add_rule(tenant, run_id, callee="api", actor="a")
    wrapper_service.add_rule(tenant, run_id, callee="api", actor="b")
    rules = wrapper_service.list_rules(tenant, run_id)
    assert len(rules) == 1 and rules[0]["actor"] == "b"  # second upsert overwrote actor


def test_add_wrapper_invalid_callee_raises(authorized_session):
    tenant, session_id = authorized_session
    run_id = _run(tenant, session_id)
    # `a.b` is a VALID dotted receiver since wrapper-teaching (this.httpClient/svc.api);
    # a hyphen is a genuine non-identifier, so it still exercises the rejection path.
    with pytest.raises(InvalidWrapperCallee):
        wrapper_service.add_rule(tenant, run_id, callee="a-b")


def test_add_wrapper_unknown_run_is_none(authorized_session):
    tenant, _session_id = authorized_session
    assert wrapper_service.add_rule(
        tenant, "00000000-0000-0000-0000-000000000000", callee="api"
    ) is None


def test_add_wrapper_reextracts_and_recovers_endpoint(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _run_with_source(redis, tenant, session_id, b"const api = makeClient(); api.get('/svc');")

    res = wrapper_service.add_rule(tenant, run_id, callee="api")

    assert res["recovered"] >= 1
    with tenant_session(tenant) as session:
        values = {
            f.value for f in session.execute(
                select(models.Finding).where(
                    models.Finding.run_id == run_id, models.Finding.type == "endpoint",
                )
            ).scalars()
        }
    assert "GET /svc" in values
