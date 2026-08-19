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
        # A SECRET and a PARAM finding must NOT count as endpoints OR suspected: both
        # host-consuming queries are explicit ALLOWLISTS, so a host on a secret/param
        # occurrence leaks into neither lane. The secret carries an in-scope host that
        # also has a real endpoint (so the assertions below prove no leak/double-count);
        # the param a host-less occurrence.
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
        # Suspected-backend lanes (DEBT D24/D26 follow-up): a generic-client call
        # (Tier 5) and an unresolved sink (Tier 4) each carry a resolved host that
        # rolls up into `suspected` (SEPARATE from confirmed endpoints). A host-less
        # unresolved sink is a suspected_unattributed, never an endpoints_unattributed.
        # A page_route (client-nav target) rolls up into its OWN `routes` column and IS
        # listed as a discovered host (Starbucks QA #5) — never into endpoints/suspected.
        generic = models.Finding(
            tenant_id=tenant,
            run_id=run_id,
            finding_hash="g1",
            type="endpoint_generic",
            value="GET https://guess.acme.io/x",
            path="app.js",
        )
        unresolved = models.Finding(
            tenant_id=tenant,
            run_id=run_id,
            finding_hash="u1",
            type="endpoint_unresolved",
            value="https://api.acme.io/maybe",
            path="app.js",
        )
        unresolved_hostless = models.Finding(
            tenant_id=tenant,
            run_id=run_id,
            finding_hash="u2",
            type="endpoint_unresolved",
            value="/relative/sink",
            path="app.js",
        )
        page_route = models.Finding(
            tenant_id=tenant,
            run_id=run_id,
            finding_hash="r1",
            type="page_route",
            value="https://cdn.mui.com/docs",
            path="app.js",
        )
        session.add_all([generic, unresolved, unresolved_hostless, page_route])
        session.flush()
        session.add_all(
            [
                models.FindingOccurrence(
                    tenant_id=tenant,
                    finding_id=str(generic.id),
                    occurrence_hash="o5",
                    host="guess.acme.io",
                ),
                models.FindingOccurrence(
                    tenant_id=tenant,
                    finding_id=str(unresolved.id),
                    occurrence_hash="o6",
                    host="api.acme.io",
                ),
                models.FindingOccurrence(
                    tenant_id=tenant,
                    finding_id=str(unresolved_hostless.id),
                    occurrence_hash="o7",
                    host=None,
                ),
                models.FindingOccurrence(
                    tenant_id=tenant,
                    finding_id=str(page_route.id),
                    occurrence_hash="o8",
                    host="cdn.mui.com",
                ),
            ]
        )
        return run_id


def test_get_hosts_aggregates_and_classifies_scope(client, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed_run(tenant, session_id)
    resp = client.get(f"/runs/{run_id}/hosts", headers=_headers(tenant))
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == run_id
    assert body["count"] == 5  # + cdn.mui.com (page_route target, now listed)
    assert body["in_scope"] == 3  # guess.acme.io in; cdn.evil.com + cdn.mui.com out
    assert body["endpoints_unattributed"] == 1  # only h2; the host-less sink is suspected
    assert body["suspected_unattributed"] == 1  # u2 (host-less unresolved), NOT the host-less param

    by_host = {h["host"]: h for h in body["hosts"]}
    # guess.acme.io joins via the suspected lane; cdn.mui.com via the page_route lane —
    # every discovered host is listed (Starbucks QA #5), sorted.
    assert list(by_host) == [
        "acme.io",
        "api.acme.io",
        "cdn.evil.com",
        "cdn.mui.com",
        "guess.acme.io",
    ]
    # The page_route host rolls up into its OWN `routes` column, out of scope, and never
    # dilutes the confirmed endpoints or suspected counts.
    assert by_host["cdn.mui.com"]["routes"] == 1
    assert by_host["cdn.mui.com"]["in_scope"] is False
    assert (by_host["cdn.mui.com"]["endpoints"], by_host["cdn.mui.com"]["suspected"]) == (0, 0)

    assert by_host["acme.io"]["in_scope"] is True
    assert (by_host["acme.io"]["assets"], by_host["acme.io"]["techs"]) == (1, 1)
    # api.acme.io carries a confirmed endpoint AND a suspected sink — counted once each;
    # the SECRET occurrence on the same host leaks into NEITHER (the allowlist proof).
    assert by_host["api.acme.io"]["endpoints"] == 1
    assert by_host["api.acme.io"]["suspected"] == 1
    assert by_host["api.acme.io"]["in_scope"] is True
    # guess.acme.io exists ONLY because of the generic lane (a suspected-only host).
    assert by_host["guess.acme.io"]["suspected"] == 1
    assert by_host["guess.acme.io"]["endpoints"] == 0
    assert by_host["guess.acme.io"]["in_scope"] is True
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
    assert body["suspected_unattributed"] == 0


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
