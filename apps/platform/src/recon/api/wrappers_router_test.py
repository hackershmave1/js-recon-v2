import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from recon import storage
from recon.api.app import create_app
from recon.db import models
from recon.db.base import tenant_session
from recon.runs import service
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


@pytest.fixture()
def client():
    return TestClient(create_app())


def _headers(tenant):
    return {"X-Tenant-Id": tenant}


def _run_with_source(redis, tenant, session_id, source: bytes) -> str:
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    key = storage.put_blob(tenant, view.id, "input", source)
    with tenant_session(tenant) as session:
        session.execute(update(models.Run).where(models.Run.id == view.id).values(input_ref=key))
    return view.id


def test_post_wrapper_recovers_and_lists_endpoint(client, redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _run_with_source(redis, tenant, session_id, b"const api = makeClient(); api.get('/svc');")

    resp = client.post(f"/runs/{run_id}/wrappers", headers=_headers(tenant), json={"callee": "api"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["rule"]["callee"] == "api" and body["recovered"] >= 1

    # Recovered endpoint is visible in the findings read (documented/visible end-to-end).
    findings = client.get(f"/runs/{run_id}/findings", headers=_headers(tenant)).json()
    assert any(f["value"] == "GET /svc" for f in findings["findings"])


def test_get_lists_wrappers(client, authorized_session):
    tenant, session_id = authorized_session
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
    client.post(f"/runs/{run_id}/wrappers", headers=_headers(tenant), json={"callee": "api"})
    resp = client.get(f"/runs/{run_id}/wrappers", headers=_headers(tenant))
    assert resp.status_code == 200 and len(resp.json()) == 1


def test_delete_wrapper(client, authorized_session):
    tenant, session_id = authorized_session
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
    rule = client.post(f"/runs/{run_id}/wrappers", headers=_headers(tenant),
                       json={"callee": "api"}).json()["rule"]
    resp = client.delete(f"/runs/{run_id}/wrappers/{rule['id']}", headers=_headers(tenant))
    assert resp.status_code == 204
    assert client.get(f"/runs/{run_id}/wrappers", headers=_headers(tenant)).json() == []


def test_invalid_callee_is_422(client, authorized_session):
    tenant, session_id = authorized_session
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
    resp = client.post(f"/runs/{run_id}/wrappers", headers=_headers(tenant), json={"callee": "a.b"})
    assert resp.status_code == 422


def test_unknown_run_is_404(client, tenant):
    resp = client.post("/runs/00000000-0000-0000-0000-000000000000/wrappers",
                       headers=_headers(tenant), json={"callee": "api"})
    assert resp.status_code == 404


def test_other_tenant_run_is_404(client, redis, authorized_session):
    owner_tenant, session_id = authorized_session
    run_id = _run_with_source(redis, owner_tenant, session_id, b"const api = makeClient(); api.get('/x');")
    other = sessions_service.create_tenant("wrapper-other")
    resp = client.post(f"/runs/{run_id}/wrappers", headers=_headers(other), json={"callee": "api"})
    assert resp.status_code == 404
