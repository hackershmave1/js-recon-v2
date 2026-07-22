import pytest
from fastapi.testclient import TestClient

from recon.api.app import create_app
from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import store

pytestmark = pytest.mark.integration


@pytest.fixture()
def client():
    return TestClient(create_app())


def _headers(tenant):
    return {"X-Tenant-Id": tenant}


def _seed(tenant, session_id):
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        result = store.record_finding(
            session, tenant_id=tenant, run_id=run_id, finding_type=FindingType.ENDPOINT,
            value="POST /api/users/{id}", path="input.js",
            occurrence=store.Occurrence(host="api.acme.io", raw_url="/api/users/42"),
            attributes={"method": "POST", "kind": "fetch"}, first_stage="analyzing",
        )
        return run_id, result.finding_hash


def test_get_requests_returns_artifacts(client, authorized_session):
    tenant, session_id = authorized_session
    run_id, _hash = _seed(tenant, session_id)
    resp = client.get(f"/runs/{run_id}/requests", headers=_headers(tenant))
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    request = body["requests"][0]
    assert request["method"] == "POST"
    assert "curl -X POST" in request["artifacts"]["curl"]
    assert request["artifacts"]["http"].startswith("POST /api/users/42 HTTP/1.1")


def test_get_requests_for_run_with_no_findings_is_empty_200(client, authorized_session):
    tenant, session_id = authorized_session
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
    resp = client.get(f"/runs/{run_id}/requests", headers=_headers(tenant))
    assert resp.status_code == 200
    assert resp.json() == {"run_id": run_id, "count": 0, "requests": []}


def test_get_requests_unknown_run_is_404(client, tenant):
    resp = client.get(
        "/runs/00000000-0000-0000-0000-000000000000/requests", headers=_headers(tenant)
    )
    assert resp.status_code == 404


def test_post_triage_confirms_and_shows_on_findings(client, authorized_session):
    tenant, session_id = authorized_session
    run_id, finding_hash = _seed(tenant, session_id)
    resp = client.post(
        f"/runs/{run_id}/findings/{finding_hash}/triage",
        json={"status": "confirmed", "actor": "tester"},
        headers=_headers(tenant),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"

    findings = client.get(f"/runs/{run_id}/findings", headers=_headers(tenant)).json()
    endpoint = next(f for f in findings["findings"] if f["finding_hash"] == finding_hash)
    assert endpoint["triage"]["status"] == "confirmed"


def test_post_triage_bad_status_is_400(client, authorized_session):
    tenant, session_id = authorized_session
    run_id, finding_hash = _seed(tenant, session_id)
    resp = client.post(
        f"/runs/{run_id}/findings/{finding_hash}/triage",
        json={"status": "bogus"},
        headers=_headers(tenant),
    )
    assert resp.status_code == 400


def test_post_triage_unknown_run_is_404(client, tenant):
    resp = client.post(
        "/runs/00000000-0000-0000-0000-000000000000/findings/" + "a" * 64 + "/triage",
        json={"status": "confirmed"},
        headers=_headers(tenant),
    )
    assert resp.status_code == 404
