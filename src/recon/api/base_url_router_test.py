import pytest
from fastapi.testclient import TestClient

from recon.api.app import create_app
from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import store
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration

_SPEC = (
    b'{"openapi":"3.0.3","info":{"title":"t","version":"0"},'
    b'"paths":{"/location/address/search":{"get":{"responses":{"default":{"description":"x"}}}}}}'
)


@pytest.fixture()
def client():
    return TestClient(create_app())


def _headers(tenant):
    return {"X-Tenant-Id": tenant}


def _seed_relative(tenant, session_id):
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        store.record_finding(
            session, tenant_id=tenant, run_id=run_id, finding_type=FindingType.ENDPOINT,
            value="GET /address/search", path="app.js",
            occurrence=store.Occurrence(host=None, raw_url="/address/search"),
            attributes={"method": "GET", "kind": "fetch"}, first_stage="analyzing",
        )
        return run_id


def test_post_prefix_rule_documents_the_endpoint(client, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed_relative(tenant, session_id)
    client.post(f"/runs/{run_id}/spec", headers=_headers(tenant), content=_SPEC)

    resp = client.post(
        f"/runs/{run_id}/base-url", headers=_headers(tenant),
        json={"kind": "prefix", "path_prefix": "/address", "base_url": "/location"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["rule"]["base_url"] == "/location"
    assert body["summary"]["documented"] == 1  # unresolved -> documented after the rule


def test_get_lists_rules(client, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed_relative(tenant, session_id)
    client.post(f"/runs/{run_id}/base-url", headers=_headers(tenant),
                json={"kind": "prefix", "path_prefix": "/address", "base_url": "/location"})
    resp = client.get(f"/runs/{run_id}/base-url", headers=_headers(tenant))
    assert resp.status_code == 200 and len(resp.json()) == 1


def test_delete_rule(client, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed_relative(tenant, session_id)
    rule = client.post(f"/runs/{run_id}/base-url", headers=_headers(tenant),
                       json={"kind": "prefix", "path_prefix": "/a", "base_url": "/b"}).json()["rule"]
    resp = client.delete(f"/runs/{run_id}/base-url/{rule['id']}", headers=_headers(tenant))
    assert resp.status_code == 204
    assert client.get(f"/runs/{run_id}/base-url", headers=_headers(tenant)).json() == []


def test_invalid_base_is_422(client, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed_relative(tenant, session_id)
    resp = client.post(f"/runs/{run_id}/base-url", headers=_headers(tenant),
                       json={"kind": "prefix", "path_prefix": "/a", "base_url": "ftp://x"})
    assert resp.status_code == 422


def test_kind_field_mismatch_is_422(client, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed_relative(tenant, session_id)
    resp = client.post(f"/runs/{run_id}/base-url", headers=_headers(tenant),
                       json={"kind": "prefix", "finding_hashes": ["h"], "base_url": "/a"})
    assert resp.status_code == 422  # prefix requires path_prefix, not finding_hashes


def test_unknown_run_is_404(client, tenant):
    resp = client.post("/runs/00000000-0000-0000-0000-000000000000/base-url",
                       headers=_headers(tenant),
                       json={"kind": "prefix", "path_prefix": "/a", "base_url": "/b"})
    assert resp.status_code == 404


def test_other_tenant_run_is_404(client, authorized_session):
    owner_tenant, session_id = authorized_session
    run_id = _seed_relative(owner_tenant, session_id)
    other = sessions_service.create_tenant("base-url-other")
    resp = client.post(f"/runs/{run_id}/base-url", headers=_headers(other),
                       json={"kind": "prefix", "path_prefix": "/a", "base_url": "/b"})
    assert resp.status_code == 404
