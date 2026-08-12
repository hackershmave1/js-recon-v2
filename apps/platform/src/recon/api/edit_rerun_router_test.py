"""Integration tests for edit-&-re-run: GET /runs/{id}/config + POST /runs/{id}/rerun.

Exercise the real routes against a live Postgres/Redis/MinIO stack — the prefill read,
the reuse-vs-fork session seam, the fresh-ack requirement on a scope change (MF1), the
cross-tenant 404 (IDOR, MF4), the non-positive-cap reject (REQ-Q5), and the upload
blob-copy. Marked ``integration`` so the pure suite still runs offline.
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


def _session(client: TestClient, tenant_id: str, *, scope: str = "acme.io") -> str:
    r = client.post(
        "/sessions",
        headers=_hdr(tenant_id),
        json={"scope_hosts": [scope], "authorized_by": "tester"},
    )
    assert r.status_code == 201, r.text
    return r.json()["session_id"]


def _crawl_run(
    client: TestClient, tenant_id: str, session_id: str, *, target: str = "acme.io"
) -> str:
    r = client.post(
        "/runs",
        headers=_hdr(tenant_id),
        json={"session_id": session_id, "target": target},
    )
    assert r.status_code == 202, r.text
    return r.json()["run_id"]


def _upload_run(client: TestClient, tenant_id: str, session_id: str) -> str:
    r = client.post(
        "/runs/upload",
        headers=_hdr(tenant_id),
        files={"file": ("input.js", b"fetch('/api/x')", "text/javascript")},
        data={"session_id": session_id},
    )
    assert r.status_code == 202, r.text
    return r.json()["run_id"]


def _run_count(client: TestClient, tenant_id: str, session_id: str) -> int:
    return client.get(f"/sessions/{session_id}/runs", headers=_hdr(tenant_id)).json()["count"]


def test_get_run_config_returns_editable_fields(tenant, redis):
    client = _client()
    session_id = _session(client, tenant)
    run_id = _crawl_run(client, tenant, session_id)

    cfg = client.get(f"/runs/{run_id}/config", headers=_hdr(tenant))
    assert cfg.status_code == 200, cfg.text
    body = cfg.json()
    assert body["target"] == "acme.io"
    assert body["crawl_mode"] is None
    assert body["scope_hosts"] == ["acme.io"]
    assert body["is_upload"] is False
    assert body["max_fetch_bytes"] is None


def test_get_run_config_404_for_unknown_run(tenant, redis):
    client = _client()
    r = client.get(f"/runs/{uuid.uuid4()}/config", headers=_hdr(tenant))
    assert r.status_code == 404


def test_rerun_no_edits_reuses_the_same_session(tenant, redis):
    client = _client()
    session_id = _session(client, tenant)
    first = _crawl_run(client, tenant, session_id)

    r = client.post(f"/runs/{first}/rerun", headers=_hdr(tenant), json={})
    assert r.status_code == 202, r.text
    assert r.json()["run_id"] != first
    assert _run_count(client, tenant, session_id) == 2  # reused => two runs in the session


def test_rerun_target_within_scope_reuses_session(tenant, redis):
    client = _client()
    session_id = _session(client, tenant)
    first = _crawl_run(client, tenant, session_id)

    r = client.post(f"/runs/{first}/rerun", headers=_hdr(tenant), json={"target": "api.acme.io"})
    assert r.status_code == 202, r.text
    assert _run_count(client, tenant, session_id) == 2  # subdomain still in scope => no fork


def test_rerun_resubmitting_same_scope_reuses_session(tenant, redis):
    client = _client()
    session_id = _session(client, tenant)
    first = _crawl_run(client, tenant, session_id)

    # The UI re-sends the prefilled scope unchanged, with NO ack — must not fork.
    r = client.post(f"/runs/{first}/rerun", headers=_hdr(tenant), json={"scope_hosts": ["acme.io"]})
    assert r.status_code == 202, r.text
    assert _run_count(client, tenant, session_id) == 2


def test_rerun_target_leaving_scope_without_ack_is_400(tenant, redis):
    client = _client()
    session_id = _session(client, tenant)
    first = _crawl_run(client, tenant, session_id)

    r = client.post(f"/runs/{first}/rerun", headers=_hdr(tenant), json={"target": "evil.example"})
    assert r.status_code == 400  # a widened scope must be re-attested (MF1)
    assert _run_count(client, tenant, session_id) == 1  # nothing created


def test_rerun_target_leaving_scope_with_ack_forks_a_new_session(tenant, redis):
    client = _client()
    session_id = _session(client, tenant)
    first = _crawl_run(client, tenant, session_id)

    r = client.post(
        f"/runs/{first}/rerun",
        headers=_hdr(tenant),
        json={"target": "evil.example", "authorized_by": "tester-2"},
    )
    assert r.status_code == 202, r.text
    # The re-run went to a NEW session (fresh scope + ack), so the original is untouched.
    assert _run_count(client, tenant, session_id) == 1


def test_rerun_scope_edit_without_ack_is_400(tenant, redis):
    client = _client()
    session_id = _session(client, tenant)
    first = _crawl_run(client, tenant, session_id)

    r = client.post(
        f"/runs/{first}/rerun",
        headers=_hdr(tenant),
        json={"scope_hosts": ["acme.io", "cdn.acme.io"]},
    )
    assert r.status_code == 400  # MF1


def test_rerun_non_positive_cap_is_rejected(tenant, redis):
    client = _client()
    session_id = _session(client, tenant)
    first = _crawl_run(client, tenant, session_id)

    r = client.post(f"/runs/{first}/rerun", headers=_hdr(tenant), json={"max_fetch_bytes": -1})
    assert r.status_code == 422  # Pydantic Field(gt=0) — a negative would fail open (REQ-Q5)


def test_rerun_cap_above_ceiling_is_rejected(tenant, redis):
    client = _client()
    session_id = _session(client, tenant)
    first = _crawl_run(client, tenant, session_id)

    # Above the 32 MiB ceiling: a clean 422, never a persisted value that overflows int4.
    huge = 4096 * 1024 * 1024
    r = client.post(f"/runs/{first}/rerun", headers=_hdr(tenant), json={"max_fetch_bytes": huge})
    assert r.status_code == 422
    assert _run_count(client, tenant, session_id) == 1  # nothing persisted


def test_rerun_cross_tenant_is_404(tenant, redis):
    client = _client()
    session_id = _session(client, tenant)
    run_id = _crawl_run(client, tenant, session_id)

    other_tenant = sessions_service.create_tenant(f"other-{uuid.uuid4().hex[:8]}")
    r = client.post(f"/runs/{run_id}/rerun", headers=_hdr(other_tenant), json={})
    assert r.status_code == 404  # not visible to another tenant (RLS / IDOR, MF4)


def test_rerun_upload_copies_bytes_into_a_new_run(tenant, redis):
    client = _client()
    session_id = _session(client, tenant)
    first = _upload_run(client, tenant, session_id)

    r = client.post(f"/runs/{first}/rerun", headers=_hdr(tenant), json={})
    assert r.status_code == 202, r.text
    assert r.json()["run_id"] != first
    assert _run_count(client, tenant, session_id) == 2
