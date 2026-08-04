import pytest

from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import store
from recon.probe import reconstruct
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


def _seed_run_with_endpoint(tenant, session_id):
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        store.record_finding(
            session, tenant_id=tenant, run_id=run_id, finding_type=FindingType.ENDPOINT,
            value="POST /api/users/{id}", path="input.js",
            occurrence=store.Occurrence(host="api.acme.io", raw_url="/api/users/42"),
            attributes={"method": "POST", "kind": "fetch"}, first_stage="analyzing",
        )
        store.record_finding(
            session, tenant_id=tenant, run_id=run_id, finding_type=FindingType.PARAM,
            value="POST /api/users/{id} body:name", path="input.js",
            occurrence=store.Occurrence(host="api.acme.io", raw_url="/api/users/42"),
            attributes={"location": "body", "name": "name"}, first_stage="analyzing",
        )
        return run_id


def test_reconstruct_run_assembles_request_from_persisted_findings():
    tenant = sessions_service.create_tenant("rec-1")
    session_view = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    run_id = _seed_run_with_endpoint(tenant, session_view.id)

    requests = reconstruct.reconstruct_run(tenant, run_id)
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.hosts == ("api.acme.io",)
    assert request.body_params == ("name",)
    assert request.content_type == "application/json"
    assert request.example_url == "/api/users/42"


def test_reconstruct_run_unknown_run_returns_none():
    tenant = sessions_service.create_tenant("rec-2")
    assert reconstruct.reconstruct_run(tenant, "00000000-0000-0000-0000-000000000000") is None
