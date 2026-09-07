import pytest
from fastapi.testclient import TestClient

from recon.api.app import create_app
from recon.auth import token as auth_token
from recon.config import get_settings
from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import store

pytestmark = pytest.mark.integration

_AUTH_KEY = "probe-actor-test-secret"


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
            session,
            tenant_id=tenant,
            run_id=run_id,
            finding_type=FindingType.ENDPOINT,
            value="POST /api/users/{id}",
            path="input.js",
            occurrence=store.Occurrence(host="api.acme.io", raw_url="/api/users/42"),
            attributes={"method": "POST", "kind": "fetch"},
            first_stage="analyzing",
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


def _seed_relative(tenant, session_id) -> str:
    """A run whose only endpoint is RELATIVE (host-less) — the case the probe
    host-selector resolves (QA #2)."""
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        store.record_finding(
            session,
            tenant_id=tenant,
            run_id=run_id,
            finding_type=FindingType.ENDPOINT,
            value="GET /api/rel",
            path="input.js",
            occurrence=store.Occurrence(host=None, raw_url="/api/rel"),
            attributes={"method": "GET", "kind": "fetch"},
            first_stage="analyzing",
        )
        return run_id


def test_get_requests_host_param_resolves_relative_endpoint(client, authorized_session):
    # QA #2: a relative (host-less) request shows a {{base_url}} placeholder by default;
    # ?host= resolves its curl + raw-HTTP against the operator-picked host at probe time.
    tenant, session_id = authorized_session
    run_id = _seed_relative(tenant, session_id)

    default = client.get(f"/runs/{run_id}/requests", headers=_headers(tenant)).json()
    assert default["requests"][0]["hosts"] == []
    assert "{{base_url}}" in default["requests"][0]["artifacts"]["curl"]

    picked = client.get(
        f"/runs/{run_id}/requests?host=chosen.example.com", headers=_headers(tenant)
    ).json()
    req = picked["requests"][0]
    assert req["hosts"] == ["chosen.example.com"]
    assert "https://chosen.example.com/api/rel" in req["artifacts"]["curl"]
    assert "Host: chosen.example.com" in req["artifacts"]["http"]


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


def test_post_triage_unknown_finding_is_404(client, authorized_session):
    tenant, session_id = authorized_session
    run_id, _hash = _seed(tenant, session_id)
    resp = client.post(
        f"/runs/{run_id}/findings/" + "b" * 64 + "/triage",
        json={"status": "confirmed"},
        headers=_headers(tenant),
    )
    assert resp.status_code == 404


# D48: actor attribution — verified JWT wins over client body field
# ----------------------------------------------------------------- #


@pytest.fixture()
def auth_client(monkeypatch):
    """A TestClient + tenant_id with RECON_AUTH_SECRET configured.

    ``get_actor`` returns the JWT's user_id; ``get_tenant_id`` also accepts the
    same JWT so the same Bearer token satisfies both deps. Clears settings cache
    before and after to avoid contaminating other tests.
    """
    monkeypatch.setenv("RECON_AUTH_SECRET", _AUTH_KEY)
    get_settings.cache_clear()
    yield TestClient(create_app())
    get_settings.cache_clear()


def _mint_for(tenant_id: str, user_id: str = "operator-jwt") -> str:
    return auth_token.mint(
        user_id=user_id,
        tenant_id=tenant_id,
        role="admin",
        key=_AUTH_KEY,
        ttl_seconds=3600,
    )


def test_triage_actor_comes_from_jwt_not_body(auth_client, authorized_session):
    """When auth is on, the triage audit actor is the JWT user_id — even when the
    body supplies a different (spoofed) actor field (D48)."""
    tenant, session_id = authorized_session
    run_id, finding_hash = _seed(tenant, session_id)
    token = _mint_for(tenant)

    resp = auth_client.post(
        f"/runs/{run_id}/findings/{finding_hash}/triage",
        json={"status": "confirmed", "actor": "spoofed-actor"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    # The actor returned comes from the JWT identity, not the body's "spoofed-actor".
    assert resp.json()["actor"] == "operator-jwt"


def test_triage_actor_falls_back_to_body_when_auth_off(client, authorized_session):
    """When auth is off (no secret), the body actor field is used as a fallback —
    dev mode / header-based tests are not broken (D48 auth-off compatibility)."""
    tenant, session_id = authorized_session
    run_id, finding_hash = _seed(tenant, session_id)

    resp = client.post(
        f"/runs/{run_id}/findings/{finding_hash}/triage",
        json={"status": "confirmed", "actor": "dev-tester"},
        headers=_headers(tenant),
    )
    assert resp.status_code == 200
    assert resp.json()["actor"] == "dev-tester"
