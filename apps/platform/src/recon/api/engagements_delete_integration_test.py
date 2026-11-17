"""Deleting an engagement detaches its sessions, under RLS (issue #262).

Live Postgres/Redis: the sessions an engagement grouped go back to "No engagement" and
are never deleted; another tenant can neither see nor delete it; and a capture still
uploading under the deleted engagement keeps landing in the SAME session (the ingest
idempotency key is the extension's session id, never the engagement).
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from recon.api.app import create_app
from recon.config import get_settings
from recon.db.base import admin_session
from recon.db.models import Tenant
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


def _hdr(tenant_id: str) -> dict:
    return {"X-Tenant-Id": tenant_id}


def _engagement(client: TestClient, tenant_id: str, name: str = "Acme Q3") -> str:
    r = client.post("/engagements", headers=_hdr(tenant_id), json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["engagement_id"]


def _session(client: TestClient, tenant_id: str, engagement_id: str | None) -> str:
    r = client.post(
        "/sessions",
        headers=_hdr(tenant_id),
        json={
            "scope_hosts": ["acme.io"],
            "authorized_by": "tester",
            "engagement_id": engagement_id,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["session_id"]


def test_delete_detaches_every_session_and_deletes_none(tenant, redis):
    client = TestClient(create_app())
    doomed = _engagement(client, tenant, "Doomed")
    kept = _engagement(client, tenant, "Kept")
    bound = [_session(client, tenant, doomed) for _ in range(2)]
    archived = _session(client, tenant, doomed)
    r = client.patch(f"/sessions/{archived}", headers=_hdr(tenant), json={"archived": True})
    assert r.status_code == 200, r.text
    other = _session(client, tenant, kept)
    solo = _session(client, tenant, None)

    # The list says up front how many sessions a delete would move (archived included),
    # which is what the confirm dialog shows.
    before = client.get("/engagements", headers=_hdr(tenant)).json()["engagements"]
    assert {e["name"]: e["session_count"] for e in before} == {"Doomed": 3, "Kept": 1}

    r = client.delete(f"/engagements/{doomed}", headers=_hdr(tenant))
    assert r.status_code == 200, r.text
    assert r.json() == {"engagement_id": doomed, "detached_sessions": 3}

    listed = client.get("/engagements", headers=_hdr(tenant)).json()
    assert [e["engagement_id"] for e in listed["engagements"]] == [kept]
    for session_id in [*bound, archived]:
        view = sessions_service.get_session(tenant, session_id)
        assert view is not None, "a session must never be deleted with its engagement"
        assert view.engagement_id is None
    assert sessions_service.get_session(tenant, other).engagement_id == kept
    assert sessions_service.get_session(tenant, solo).engagement_id is None

    # Gone is gone: a second delete is a 404, not a zero-count success.
    assert client.delete(f"/engagements/{doomed}", headers=_hdr(tenant)).status_code == 404


def test_delete_is_tenant_isolated(tenant, redis):
    client = TestClient(create_app())
    engagement_id = _engagement(client, tenant)
    session_id = _session(client, tenant, engagement_id)
    intruder = sessions_service.create_tenant(f"other-{uuid.uuid4().hex[:8]}")

    # RLS hides the row from another tenant: a 404, and nothing changes for the owner.
    r = client.delete(f"/engagements/{engagement_id}", headers=_hdr(intruder))
    assert r.status_code == 404
    assert client.get("/engagements", headers=_hdr(tenant)).json()["count"] == 1
    assert sessions_service.get_session(tenant, session_id).engagement_id == engagement_id


def test_unknown_or_malformed_id_is_404(tenant, redis):
    client = TestClient(create_app())
    for bad in (str(uuid.uuid4()), "not-a-uuid"):
        assert client.delete(f"/engagements/{bad}", headers=_hdr(tenant)).status_code == 404


def _captured_file(ext_session: str, name: str) -> dict:
    content = f"{name}();"
    return {
        "url": f"https://acme.io/{name}.js",
        "content": content,
        "sessionId": ext_session,
        "contentHash": hashlib.sha256(content.encode()).hexdigest()[:16],
        "contentLength": len(content),
    }


def test_a_capture_bound_to_a_deleted_engagement_keeps_uploading_into_its_session(
    monkeypatch, redis
):
    # The extension holds the engagement id in memory and keeps sending it as projectId.
    # After the delete its batches must still land in the SAME (now standalone) session:
    # no 4xx (would drop un-recapturable JS), no new session, no re-binding.
    monkeypatch.setenv("RECON_ENABLE_CAPTURE_INGEST", "true")
    tenant_name = f"capture-test-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("RECON_CAPTURE_TENANT_NAME", tenant_name)
    get_settings.cache_clear()
    try:
        client = TestClient(create_app())
        pid = client.post("/api/projects", json={"name": "P", "defaults": {}}).json()["id"]
        ext_session = f"sess-{uuid.uuid4().hex[:8]}"

        def _upload(name: str) -> dict:
            r = client.post(
                "/api/save-files",
                json={
                    "metadata": {"sessionId": ext_session, "projectId": pid},
                    "files": [_captured_file(ext_session, name)],
                },
            )
            assert r.status_code == 200, r.text
            return r.json()

        first = _upload("a")
        with admin_session() as db:
            tenant_id = str(db.scalar(select(Tenant.id).where(Tenant.name == tenant_name)))
        assert sessions_service.get_session(tenant_id, first["sessionId"]).engagement_id == pid

        r = client.delete(f"/engagements/{pid}", headers=_hdr(tenant_id))
        assert r.status_code == 200, r.text
        assert r.json()["detached_sessions"] == 1

        second = _upload("b")
        assert second["sessionId"] == first["sessionId"]
        assert second["stored"] == 1
        assert sessions_service.get_session(tenant_id, second["sessionId"]).engagement_id is None
    finally:
        get_settings.cache_clear()
