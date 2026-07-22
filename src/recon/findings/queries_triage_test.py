import pytest

from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import queries, store
from recon.probe import triage
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


def _run_with_endpoint(tenant, session_id):
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        result = store.record_finding(
            session, tenant_id=tenant, run_id=run_id, finding_type=FindingType.ENDPOINT,
            value="GET /orders", path="input.js",
            occurrence=store.Occurrence(host="api.acme.io", raw_url="/orders"),
            attributes={"method": "GET", "kind": "fetch"}, first_stage="analyzing",
        )
        return run_id, result.finding_hash


def test_findings_read_carries_triage_and_survives_a_rerun():
    tenant = sessions_service.create_tenant("join-1")
    session_view = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    run_one, finding_hash = _run_with_endpoint(tenant, session_view.id)

    triage.set_triage_for_run(tenant, run_one, finding_hash, status="confirmed", actor="t")

    # A NEW run in the same session re-produces the same finding_hash; the verdict
    # must still attach — proof that triage is session+hash scoped, not run scoped.
    run_two, finding_hash_two = _run_with_endpoint(tenant, session_view.id)
    assert finding_hash_two == finding_hash

    view = queries.list_findings(tenant, run_two)
    endpoint = next(f for f in view.findings if f.finding_hash == finding_hash)
    assert endpoint.triage is not None
    assert endpoint.triage.status == "confirmed"


def test_untriaged_finding_reads_as_none():
    tenant = sessions_service.create_tenant("join-2")
    session_view = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    run_id, _hash = _run_with_endpoint(tenant, session_view.id)
    view = queries.list_findings(tenant, run_id)
    assert view.findings[0].triage is None
