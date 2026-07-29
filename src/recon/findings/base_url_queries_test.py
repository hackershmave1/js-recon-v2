import pytest

from recon.db import models
from recon.db.base import tenant_session
from recon.findings import queries

pytestmark = pytest.mark.integration


def _add_run(session, tenant, session_id):
    run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
    session.add(run)
    session.flush()
    return str(run.id)


def test_list_rules_for_run_returns_typed_rules(authorized_session):
    tenant, session_id = authorized_session
    with tenant_session(tenant) as session:
        run_id = _add_run(session, tenant, session_id)
        session.add(models.SessionBaseUrl(
            tenant_id=tenant, session_id=session_id, kind="prefix",
            path_prefix="/address", base_url="/location",
        ))
        session.add(models.SessionBaseUrl(
            tenant_id=tenant, session_id=session_id, kind="selection",
            finding_hashes=["abc"], base_url="https://api.example.com",
        ))

    rules = queries.list_base_url_rules(tenant, run_id)
    kinds = {r.kind for r in rules}
    assert kinds == {"prefix", "selection"}
    prefix = next(r for r in rules if r.kind == "prefix")
    assert prefix.path_prefix == "/address" and prefix.base_url == "/location"
    selection = next(r for r in rules if r.kind == "selection")
    assert selection.finding_hashes == ("abc",)


def test_list_rules_unknown_run_is_empty(authorized_session):
    tenant, _session_id = authorized_session
    assert queries.list_base_url_rules(tenant, "00000000-0000-0000-0000-000000000000") == []
