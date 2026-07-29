import pytest

from recon.db import models
from recon.db.base import tenant_session
from recon.findings.base_url import InvalidBaseUrl
from recon.spec import base_url_service

pytestmark = pytest.mark.integration


def _run(tenant, session_id):
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        return str(run.id)


def test_add_list_delete_prefix_rule(authorized_session):
    tenant, session_id = authorized_session
    run_id = _run(tenant, session_id)

    res = base_url_service.add_rule(
        tenant, run_id, kind="prefix", base_url="/location", path_prefix="/address",
    )
    assert res["rule"]["kind"] == "prefix" and res["rule"]["base_url"] == "/location"

    rules = base_url_service.list_rules(tenant, run_id)
    assert len(rules) == 1 and rules[0]["path_prefix"] == "/address"

    assert base_url_service.delete_rule(tenant, run_id, res["rule"]["id"]) is True
    assert base_url_service.list_rules(tenant, run_id) == []


def test_add_prefix_rule_upserts_on_prefix(authorized_session):
    tenant, session_id = authorized_session
    run_id = _run(tenant, session_id)
    base_url_service.add_rule(tenant, run_id, kind="prefix", base_url="/a", path_prefix="/p")
    base_url_service.add_rule(tenant, run_id, kind="prefix", base_url="/b", path_prefix="/p")
    rules = base_url_service.list_rules(tenant, run_id)
    assert len(rules) == 1 and rules[0]["base_url"] == "/b"  # second overwrote the first


def test_add_rule_invalid_base_raises(authorized_session):
    tenant, session_id = authorized_session
    run_id = _run(tenant, session_id)
    with pytest.raises(InvalidBaseUrl):
        base_url_service.add_rule(tenant, run_id, kind="prefix", base_url="ftp://x", path_prefix="/p")


def test_add_rule_unknown_run_is_none(authorized_session):
    tenant, _session_id = authorized_session
    assert base_url_service.add_rule(
        tenant, "00000000-0000-0000-0000-000000000000",
        kind="prefix", base_url="/a", path_prefix="/p",
    ) is None
