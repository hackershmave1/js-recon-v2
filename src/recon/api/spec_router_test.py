import pytest
from fastapi.testclient import TestClient

from recon.api.app import create_app
from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import store
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration

_SUMMARY_KEYS = {
    "documented", "shadow", "unresolved", "suffix_verify", "base_url_incompleteness_ratio",
}

# A minimal, schema-valid OpenAPI 3.0 doc documenting exactly one operation --
# mirrors `spec/service_test.py`'s OPENAPI_WITH_LOCATION fixture.
OPENAPI_SPEC = b"""openapi: 3.0.0
info: {title: t, version: '1'}
paths: {/location/address/search: {get: {responses: {'200': {description: ok}}}}}
"""


@pytest.fixture()
def client():
    return TestClient(create_app())


def _headers(tenant):
    return {"X-Tenant-Id": tenant}


def _seed(tenant, session_id):
    """A run with one ENDPOINT finding the spec above documents exactly."""
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        store.record_finding(
            session, tenant_id=tenant, run_id=run_id, finding_type=FindingType.ENDPOINT,
            value="GET /location/address/search", path="input.js",
            occurrence=store.Occurrence(host="acme.io", raw_url="/location/address/search"),
            attributes={"method": "GET", "kind": "fetch"}, first_stage="analyzing",
        )
        return run_id


def test_post_spec_classifies_and_returns_summary(client, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed(tenant, session_id)

    resp = client.post(
        f"/runs/{run_id}/spec",
        content=OPENAPI_SPEC,
        headers={**_headers(tenant), "Content-Type": "application/yaml"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == _SUMMARY_KEYS
    assert body["documented"] == 1
    assert body["shadow"] == 0


def test_post_spec_unknown_run_is_404(client, tenant):
    resp = client.post(
        "/runs/00000000-0000-0000-0000-000000000000/spec",
        content=b"{}",
        headers=_headers(tenant),
    )
    assert resp.status_code == 404


def test_post_spec_other_tenant_run_is_404(client, authorized_session):
    owner_tenant, session_id = authorized_session
    run_id = _seed(owner_tenant, session_id)
    # A genuinely distinct second tenant -- NOT the `tenant` fixture, since that
    # would resolve to the SAME cached instance `authorized_session` already
    # depends on within this one test call (mirrors `spec/service_test.py`'s
    # `test_tenant_isolation_on_finding_spec_status` two-tenant setup).
    other_tenant = sessions_service.create_tenant("spec-router-other")

    resp = client.post(
        f"/runs/{run_id}/spec",
        content=OPENAPI_SPEC,
        headers=_headers(other_tenant),
    )

    assert resp.status_code == 404


def test_post_invalid_spec_is_422(client, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed(tenant, session_id)

    resp = client.post(
        f"/runs/{run_id}/spec",
        content=b"not a spec",
        headers=_headers(tenant),
    )

    assert resp.status_code == 422
    assert "invalid spec" in resp.json()["detail"]
