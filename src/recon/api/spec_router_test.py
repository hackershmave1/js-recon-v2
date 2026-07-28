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


def _seed_documented_and_shadow(tenant, session_id):
    """A run with one finding the spec above documents exactly, plus one it
    documents nowhere -- so the findings read shows both verdicts at once."""
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        documented = store.record_finding(
            session, tenant_id=tenant, run_id=run_id, finding_type=FindingType.ENDPOINT,
            value="GET /location/address/search", path="input.js",
            occurrence=store.Occurrence(host="acme.io", raw_url="/location/address/search"),
            attributes={"method": "GET", "kind": "fetch"}, first_stage="analyzing",
        )
        shadow = store.record_finding(
            session, tenant_id=tenant, run_id=run_id, finding_type=FindingType.ENDPOINT,
            value="POST /admin/wipe", path="input.js",
            occurrence=store.Occurrence(host="acme.io", raw_url="/admin/wipe"),
            attributes={"method": "POST", "kind": "fetch"}, first_stage="analyzing",
        )
        return run_id, documented.finding_hash, shadow.finding_hash


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


def test_get_findings_reflects_spec_status_and_summary_after_attach(client, authorized_session):
    # Task 10: GET /runs/{run_id}/findings surfaces the per-finding spec_status
    # block (matching the classify verdict this router's own POST just wrote)
    # and a top-level "spec" summary with the same 5 bucket-count keys the
    # POST response carries, scoped to this run's own endpoint findings.
    tenant, session_id = authorized_session
    run_id, documented_hash, shadow_hash = _seed_documented_and_shadow(tenant, session_id)

    client.post(
        f"/runs/{run_id}/spec",
        content=OPENAPI_SPEC,
        headers={**_headers(tenant), "Content-Type": "application/yaml"},
    )

    resp = client.get(f"/runs/{run_id}/findings", headers=_headers(tenant))
    assert resp.status_code == 200
    body = resp.json()

    findings_by_hash = {f["finding_hash"]: f for f in body["findings"]}
    assert findings_by_hash[documented_hash]["spec_status"] == {
        "status": "documented",
        "reason": "documented",
        "matched_operation": "GET /location/address/search",
    }
    shadow_status = findings_by_hash[shadow_hash]["spec_status"]
    assert shadow_status["status"] == "shadow"
    assert shadow_status["matched_operation"] is None

    assert set(body["spec"]) == _SUMMARY_KEYS
    assert body["spec"]["documented"] == 1
    assert body["spec"]["shadow"] == 1


def test_get_findings_spec_status_is_unclassified_without_attach(client, authorized_session):
    # No POST /spec at all -> every finding renders "unclassified" (spec_status
    # null) and the top-level "spec" block is null, not an all-zero summary.
    tenant, session_id = authorized_session
    run_id = _seed(tenant, session_id)

    resp = client.get(f"/runs/{run_id}/findings", headers=_headers(tenant))
    assert resp.status_code == 200
    body = resp.json()

    assert body["spec"] is None
    assert all(f["spec_status"] is None for f in body["findings"])
