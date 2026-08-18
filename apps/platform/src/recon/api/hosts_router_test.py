"""Integration tests for GET /runs/{id}/hosts (DEBT D26).

Live PG (RLS): seeds a run's assets / endpoint findings / tech, then asserts the
aggregated inventory, the in/out-of-scope classification against the session's
scope_hosts=["acme.io"], and the RLS 404s — mirroring tech_router_test.py.
"""

import pytest
from fastapi.testclient import TestClient

from recon.api.app import create_app
from recon.db import models
from recon.db.base import tenant_session
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


@pytest.fixture()
def client():
    return TestClient(create_app())


def _headers(tenant):
    return {"X-Tenant-Id": tenant}


def _endpoint(tenant, run_id, finding_hash, host):
    """An endpoint finding (its occurrence, carrying `host` or None, is added separately)."""
    return models.Finding(
        tenant_id=tenant,
        run_id=run_id,
        finding_hash=finding_hash,
        type="endpoint",
        value=f"https://{host or 'x'}/{finding_hash}",
        path="app.js",
    )


def _seed_run(tenant, session_id) -> str:
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        # Two assets: an in-scope host and an out-of-scope third-party host.
        session.add(
            models.RunAsset(
                tenant_id=tenant,
                run_id=run_id,
                url="https://acme.io/app.js",
                fetch_status="ok",
                analyze_status="ok",
            )
        )
        session.add(
            models.RunAsset(
                tenant_id=tenant,
                run_id=run_id,
                url="https://cdn.evil.com/x.js",
                fetch_status="ok",
                analyze_status="ok",
            )
        )
        session.add(
            models.RunTechnology(
                tenant_id=tenant,
                run_id=run_id,
                host="acme.io",
                name="Nginx",
                categories=["Web servers"],
                version=None,
                confidence=100,
                evidence=[],
            )
        )
        # One endpoint resolved to an in-scope subdomain, one with no host (relative).
        resolved = _endpoint(tenant, run_id, "h1", "api.acme.io")
        unattributed = _endpoint(tenant, run_id, "h2", None)
        session.add_all([resolved, unattributed])
        session.flush()
        session.add(
            models.FindingOccurrence(
                tenant_id=tenant,
                finding_id=str(resolved.id),
                occurrence_hash="o1",
                host="api.acme.io",
            )
        )
        session.add(
            models.FindingOccurrence(
                tenant_id=tenant,
                finding_id=str(unattributed.id),
                occurrence_hash="o2",
                host=None,
            )
        )
        # A SECRET and a PARAM finding must NOT count as endpoints (the query filters
        # type=="endpoint"). The secret carries an in-scope host that already exists
        # via the endpoint; the param a host-less occurrence — neither may move any
        # host's endpoint count or endpoints_unattributed.
        secret = models.Finding(
            tenant_id=tenant,
            run_id=run_id,
            finding_hash="s1",
            type="secret",
            value="AKIAEXAMPLE",
            path="app.js",
        )
        param = models.Finding(
            tenant_id=tenant,
            run_id=run_id,
            finding_hash="p1",
            type="param",
            value="token",
            path="app.js",
        )
        session.add_all([secret, param])
        session.flush()
        session.add(
            models.FindingOccurrence(
                tenant_id=tenant,
                finding_id=str(secret.id),
                occurrence_hash="o3",
                host="api.acme.io",
            )
        )
        session.add(
            models.FindingOccurrence(
                tenant_id=tenant,
                finding_id=str(param.id),
                occurrence_hash="o4",
                host=None,
            )
        )
        return run_id


def test_get_hosts_aggregates_and_classifies_scope(client, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed_run(tenant, session_id)
    resp = client.get(f"/runs/{run_id}/hosts", headers=_headers(tenant))
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == run_id
    assert body["count"] == 3  # acme.io, api.acme.io, cdn.evil.com
    assert body["in_scope"] == 2  # acme.io + api.acme.io (subdomain of the scope)
    assert body["endpoints_unattributed"] == 1  # the host-less endpoint (h2)

    by_host = {h["host"]: h for h in body["hosts"]}
    assert list(by_host) == ["acme.io", "api.acme.io", "cdn.evil.com"]  # host-asc order

    assert by_host["acme.io"]["in_scope"] is True
    assert (by_host["acme.io"]["assets"], by_host["acme.io"]["techs"]) == (1, 1)
    assert by_host["api.acme.io"]["endpoints"] == 1 and by_host["api.acme.io"]["in_scope"] is True
    assert by_host["cdn.evil.com"]["in_scope"] is False
    assert by_host["cdn.evil.com"]["assets"] == 1


def test_unknown_run_is_404(client, tenant):
    resp = client.get("/runs/00000000-0000-0000-0000-000000000000/hosts", headers=_headers(tenant))
    assert resp.status_code == 404


def test_get_hosts_empty_when_run_has_nothing(client, authorized_session):
    # A real run with no assets/findings/tech is 200 with an empty inventory,
    # distinct from the 404 unknown-run case above.
    tenant, session_id = authorized_session
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)

    resp = client.get(f"/runs/{run_id}/hosts", headers=_headers(tenant))
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0 and body["hosts"] == []
    assert body["in_scope"] == 0 and body["endpoints_unattributed"] == 0


def test_declared_base_url_host_is_flagged(client, authorized_session):
    # An operator base-URL rule (REQ-C2) declares a host that may have no directly
    # attributed asset/endpoint/tech: it must still appear, flagged `declared`, and
    # be scope-classified — a zero-count row an operator recognizes as "I declared this".
    tenant, session_id = authorized_session
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        session.add(
            models.SessionBaseUrl(
                tenant_id=tenant,
                session_id=session_id,
                kind="prefix",
                path_prefix="/api",
                base_url="https://declared.acme.io",
            )
        )

    resp = client.get(f"/runs/{run_id}/hosts", headers=_headers(tenant))
    assert resp.status_code == 200
    by_host = {h["host"]: h for h in resp.json()["hosts"]}
    assert "declared.acme.io" in by_host
    row = by_host["declared.acme.io"]
    assert row["declared"] is True and row["in_scope"] is True  # subdomain of acme.io
    assert (row["assets"], row["endpoints"], row["techs"]) == (0, 0, 0)


def test_other_tenant_sees_none(client, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed_run(tenant, session_id)
    other = sessions_service.create_tenant("hosts-other")
    resp = client.get(f"/runs/{run_id}/hosts", headers=_headers(other))
    assert resp.status_code == 404  # RLS -> run invisible -> None -> 404
