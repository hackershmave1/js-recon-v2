"""Integration tests for the R6 Sessions + Engagements surface.

Exercise the real routes against a live Postgres/Redis/MinIO stack: the Sessions
card list with latest-run stats, tenant isolation (RLS), rename/archive/delete,
the runs list, re-run (both the no-run 400 and a real upload re-run), and the
engagement tier. Marked ``integration`` so the pure suite still runs offline.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from recon.api.app import create_app
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


def _client() -> TestClient:
    return TestClient(create_app())


def _hdr(tenant_id: str) -> dict:
    return {"X-Tenant-Id": tenant_id}


def _new_session(client: TestClient, tenant_id: str, *, scope: str = "acme.io") -> str:
    r = client.post(
        "/sessions",
        headers=_hdr(tenant_id),
        json={"scope_hosts": [scope], "authorized_by": "tester"},
    )
    assert r.status_code == 201, r.text
    return r.json()["session_id"]


def _upload_run(client: TestClient, tenant_id: str, session_id: str) -> str:
    r = client.post(
        "/runs/upload",
        headers=_hdr(tenant_id),
        files={"file": ("input.js", b"fetch('/api/x')", "text/javascript")},
        data={"session_id": session_id},
    )
    assert r.status_code == 202, r.text
    return r.json()["run_id"]


def test_list_sessions_returns_card_with_latest_run_stats(tenant, redis):
    client = _client()
    session_id = _new_session(client, tenant)
    run_id = _upload_run(client, tenant, session_id)

    r = client.get("/sessions", headers=_hdr(tenant))
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    card = body["sessions"][0]
    assert card["session_id"] == session_id
    # Uploads have no target, no rename yet -> host falls back to the scope host (M3).
    assert card["host"] == "acme.io"
    assert card["latest_run"]["run_id"] == run_id
    # A single-blob upload is one file; no findings yet -> honest zeros; analyze
    # hasn't emitted -> coverage is unknown ("—"), never a faked number (M1/M2).
    assert card["files"] == 1
    assert card["endpoints"] == 0
    assert card["secrets"] == 0
    assert card["coverage_pct"] is None


def test_sessions_are_tenant_isolated(tenant, redis):
    client = _client()
    mine = _new_session(client, tenant)
    other_tenant = sessions_service.create_tenant(f"other-{uuid.uuid4().hex[:8]}")
    _new_session(client, other_tenant, scope="evil.test")

    r = client.get("/sessions", headers=_hdr(tenant))
    ids = [s["session_id"] for s in r.json()["sessions"]]
    assert ids == [mine]  # the other tenant's session is invisible (RLS)


def test_rename_archive_and_delete(tenant, redis):
    client = _client()
    session_id = _new_session(client, tenant)

    # Rename -> the card's host label reflects it (name-first, M3).
    r = client.patch(f"/sessions/{session_id}", headers=_hdr(tenant), json={"name": "Acme prod"})
    assert r.status_code == 200 and r.json()["name"] == "Acme prod"
    card = client.get("/sessions", headers=_hdr(tenant)).json()["sessions"][0]
    assert card["host"] == "Acme prod"

    # Archive -> hidden by default, visible with ?archived=true.
    r = client.patch(f"/sessions/{session_id}", headers=_hdr(tenant), json={"archived": True})
    assert r.status_code == 200 and r.json()["archived"] is True
    assert client.get("/sessions", headers=_hdr(tenant)).json()["count"] == 0
    shown = client.get("/sessions?archived=true", headers=_hdr(tenant)).json()
    assert shown["count"] == 1 and shown["sessions"][0]["archived"] is True

    # Delete -> gone, even from the archived view.
    assert client.delete(f"/sessions/{session_id}", headers=_hdr(tenant)).status_code == 204
    assert client.get("/sessions?archived=true", headers=_hdr(tenant)).json()["count"] == 0


def test_delete_session_with_runs_cascades(tenant, redis):
    # Regression: deleting a session that HAS runs must DB-cascade (run.session_id
    # is ON DELETE CASCADE) rather than SQLAlchemy nullifying the child runs first —
    # the NOT NULL column rejected that and 500'd delete_session. The prior delete
    # test above used a run-less session, so this path was never exercised.
    client = _client()
    session_id = _new_session(client, tenant)
    _upload_run(client, tenant, session_id)
    assert client.get(f"/sessions/{session_id}/runs", headers=_hdr(tenant)).status_code == 200

    r = client.delete(f"/sessions/{session_id}", headers=_hdr(tenant))
    assert r.status_code == 204, r.text
    # Session gone, and its run cascaded away (the runs listing 404s with the session).
    assert client.get("/sessions?archived=true", headers=_hdr(tenant)).json()["count"] == 0
    assert client.get(f"/sessions/{session_id}/runs", headers=_hdr(tenant)).status_code == 404


def test_rename_empty_is_400(tenant, redis):
    client = _client()
    session_id = _new_session(client, tenant)
    r = client.patch(f"/sessions/{session_id}", headers=_hdr(tenant), json={"name": "  "})
    assert r.status_code == 400


def test_patch_and_runs_404_for_unknown_or_cross_tenant(tenant, redis):
    client = _client()
    session_id = _new_session(client, tenant)
    other = sessions_service.create_tenant(f"other-{uuid.uuid4().hex[:8]}")

    missing = str(uuid.uuid4())
    assert client.get(f"/sessions/{missing}/runs", headers=_hdr(tenant)).status_code == 404
    assert (
        client.patch(f"/sessions/{missing}", headers=_hdr(tenant), json={"name": "x"}).status_code
        == 404
    )
    # Cross-tenant: the session exists, but not for this tenant (RLS) -> 404.
    assert client.get(f"/sessions/{session_id}/runs", headers=_hdr(other)).status_code == 404
    assert client.delete(f"/sessions/{session_id}", headers=_hdr(other)).status_code == 404


def test_rerun_400_when_session_has_no_run(tenant, redis):
    client = _client()
    session_id = _new_session(client, tenant)
    r = client.post(f"/sessions/{session_id}/rerun", headers=_hdr(tenant))
    assert r.status_code == 400


def test_rerun_from_upload_creates_a_new_run(tenant, redis):
    client = _client()
    session_id = _new_session(client, tenant)
    first = _upload_run(client, tenant, session_id)

    r = client.post(f"/sessions/{session_id}/rerun", headers=_hdr(tenant))
    assert r.status_code == 202, r.text
    second = r.json()["run_id"]
    assert second != first
    # The session now accrues two runs (newest first).
    runs = client.get(f"/sessions/{session_id}/runs", headers=_hdr(tenant)).json()
    assert runs["count"] == 2
    assert runs["runs"][0]["run_id"] == second


def test_engagements_create_list_and_isolation(tenant, redis):
    client = _client()
    r = client.post(
        "/engagements",
        headers=_hdr(tenant),
        json={
            "name": "Acme Q3",
            "in_scope_domains": ["acme.io", "api.acme.io"],
            "out_of_scope_domains": ["blog.acme.io"],
        },
    )
    assert r.status_code == 201, r.text
    engagement_id = r.json()["engagement_id"]
    assert r.json()["in_scope_domains"] == ["acme.io", "api.acme.io"]

    listed = client.get("/engagements", headers=_hdr(tenant)).json()
    assert listed["count"] == 1 and listed["engagements"][0]["engagement_id"] == engagement_id

    other = sessions_service.create_tenant(f"other-{uuid.uuid4().hex[:8]}")
    assert client.get("/engagements", headers=_hdr(other)).json()["count"] == 0


def test_empty_engagement_name_is_400(tenant, redis):
    client = _client()
    r = client.post("/engagements", headers=_hdr(tenant), json={"name": "   "})
    assert r.status_code == 400


def test_session_rejects_another_tenants_engagement(tenant, redis):
    client = _client()
    other = sessions_service.create_tenant(f"other-{uuid.uuid4().hex[:8]}")
    foreign = client.post("/engagements", headers=_hdr(other), json={"name": "Theirs"}).json()[
        "engagement_id"
    ]
    # RLS hides the engagement from `tenant`, so attaching a session to it is rejected
    # (a clean 400, not a silent inert cross-tenant FK reference).
    r = client.post(
        "/sessions",
        headers=_hdr(tenant),
        json={"scope_hosts": ["acme.io"], "authorized_by": "tester", "engagement_id": foreign},
    )
    assert r.status_code == 400


def test_session_carries_its_engagement_id(tenant, redis):
    client = _client()
    engagement_id = client.post(
        "/engagements", headers=_hdr(tenant), json={"name": "Acme Q3"}
    ).json()["engagement_id"]
    r = client.post(
        "/sessions",
        headers=_hdr(tenant),
        json={
            "scope_hosts": ["acme.io"],
            "authorized_by": "tester",
            "engagement_id": engagement_id,
        },
    )
    assert r.status_code == 201
    assert r.json()["engagement_id"] == engagement_id
    card = client.get("/sessions", headers=_hdr(tenant)).json()["sessions"][0]
    assert card["engagement_id"] == engagement_id
