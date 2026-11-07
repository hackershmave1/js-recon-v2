"""The extension's capture-lifecycle contract: capture/start → save-files(runId) → analyze/start
(runId) → more files → capture/end. Regression for the stray one-file run a late file opened
after Analyze sealed the capture."""

from __future__ import annotations

import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from recon.api.app import create_app
from recon.config import get_settings
from recon.db.base import admin_session, tenant_session
from recon.db.models import Run, RunAsset, Tenant

pytestmark = pytest.mark.integration


@pytest.fixture()
def client(monkeypatch, redis):
    name = f"capture-life-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("RECON_ENABLE_CAPTURE_INGEST", "true")
    monkeypatch.setenv("RECON_CAPTURE_TENANT_NAME", name)
    get_settings.cache_clear()
    c = TestClient(create_app())
    c.tenant_name = name  # type: ignore[attr-defined]
    yield c
    get_settings.cache_clear()


def _tenant(c) -> str:
    with admin_session() as s:
        return str(s.scalar(select(Tenant.id).where(Tenant.name == c.tenant_name)))


def _file(url: str, content: str, sid: str) -> dict:
    return {
        "url": url,
        "content": content,
        "sessionId": sid,
        "contentHash": hashlib.sha256(content.encode()).hexdigest()[:16],
        "contentLength": len(content),
    }


def _start(c, sid) -> str:
    r = c.post(f"/api/sessions/{sid}/capture/start", json={"rootDomains": ["acme.io"]})
    assert r.status_code == 200, r.text
    return r.json()["runId"]


def _save(c, sid, run_id, *files):
    return c.post(
        "/api/save-files",
        json={"metadata": {"sessionId": sid, "runId": run_id}, "files": list(files)},
    )


def _runs(c) -> list[tuple[str, str | None]]:
    tenant = _tenant(c)
    with tenant_session(tenant) as s:
        return [
            (str(r.id), r.capture_external_id)
            for r in s.scalars(select(Run).order_by(Run.created_at)).all()
        ]


def _files_in(c, run_id) -> int:
    with tenant_session(_tenant(c)) as s:
        return s.scalar(select(func.count(RunAsset.id)).where(RunAsset.run_id == run_id))


def test_a_late_file_after_analyze_joins_the_same_capture(client):
    sid = str(uuid.uuid4())
    run_id = _start(client, sid)
    assert (
        _save(client, sid, run_id, _file("https://app.acme.io/a.js", "a()", sid)).status_code == 200
    )

    r = client.post(f"/api/sessions/{sid}/analyze/start", json={"runId": run_id})
    assert r.status_code == 200 and r.json()["started"] is True
    late = _save(client, sid, run_id, _file("https://app.acme.io/b.js", "b()", sid))

    assert late.status_code == 200 and late.json()["runId"] == run_id
    assert _runs(client) == [(run_id, sid)]  # one run, still open — no stray run
    assert _files_in(client, run_id) == 2


def test_a_new_file_after_end_is_refused_not_a_new_run(client):
    sid = str(uuid.uuid4())
    run_id = _start(client, sid)
    _save(client, sid, run_id, _file("https://app.acme.io/a.js", "a()", sid))
    r = client.post(f"/api/sessions/{sid}/capture/end", json={"runId": run_id})
    assert r.status_code == 200, r.text

    late = _save(client, sid, run_id, _file("https://app.acme.io/b.js", "b()", sid))

    assert late.status_code == 409
    assert late.json()["detail"] == "capture_ended"
    assert _runs(client) == [(run_id, None)]


def test_a_retried_batch_after_end_is_harmless(client):
    sid = str(uuid.uuid4())
    run_id = _start(client, sid)
    batch = _file("https://app.acme.io/a.js", "a()", sid)
    _save(client, sid, run_id, batch)
    client.post(f"/api/sessions/{sid}/capture/end", json={"runId": run_id})

    retry = _save(client, sid, run_id, batch)

    assert retry.status_code == 200
    assert retry.json()["stored"] == 0
    assert _runs(client) == [(run_id, None)]


def test_starting_again_ends_the_capture_left_open(client):
    sid = str(uuid.uuid4())
    first = _start(client, sid)
    _save(client, sid, first, _file("https://app.acme.io/a.js", "a()", sid))

    second = _start(client, sid)

    assert second != first
    assert _runs(client) == [(first, None), (second, sid)]


def test_end_of_an_unknown_session_is_404(client):
    r = client.post(f"/api/sessions/{uuid.uuid4()}/capture/end", json={"runId": str(uuid.uuid4())})
    assert r.status_code == 404


def test_end_of_another_sessions_run_is_404(client):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    run_a = _start(client, a)
    _start(client, b)
    r = client.post(f"/api/sessions/{b}/capture/end", json={"runId": run_a})
    assert r.status_code == 404


# --- Start screen: Continue lookup + Add to scope ---------------------------------------------

_AUTH_KEY = "test-auth-secret"


def _operator(monkeypatch):
    from recon.auth import token as auth_token
    from recon.db.models import Tenant

    monkeypatch.setenv("RECON_AUTH_SECRET", _AUTH_KEY)
    get_settings.cache_clear()
    with admin_session() as s:
        tenant = Tenant(name=f"op-{uuid.uuid4().hex[:8]}")
        s.add(tenant)
        s.flush()
        tenant_id = str(tenant.id)
    token = auth_token.mint(
        user_id=str(uuid.uuid4()), tenant_id=tenant_id, role="admin", key=_AUTH_KEY, ttl_seconds=600
    )
    return TestClient(create_app()), {"Authorization": f"Bearer {token}"}


def test_lookup_finds_the_session_for_a_scope(client, monkeypatch):
    c, auth = _operator(monkeypatch)
    sid = str(uuid.uuid4())
    r = c.post(
        f"/api/sessions/{sid}/capture/start", json={"rootDomains": ["acme.io"]}, headers=auth
    )
    assert r.status_code == 200, r.text

    found = c.get("/api/capture/sessions", params={"scope": "acme.io"}, headers=auth)

    assert found.status_code == 200, found.text
    (match,) = found.json()["sessions"]
    assert match["external_id"] == sid
    assert match["captures"] == 1
    assert c.get(
        "/api/capture/sessions", params={"scope": "beta.example"}, headers=auth
    ).json() == {"sessions": []}


def test_lookup_requires_a_login(client):
    r = client.get("/api/capture/sessions", params={"scope": "acme.io"})
    assert r.status_code == 401


def test_add_to_scope(client):
    sid = str(uuid.uuid4())
    _start(client, sid)

    r = client.post(f"/api/sessions/{sid}/scope", json={"add": "beta.example"})
    bad = client.post(f"/api/sessions/{sid}/scope", json={"add": "*"})
    unknown = client.post(f"/api/sessions/{uuid.uuid4()}/scope", json={"add": "beta.example"})

    assert r.status_code == 200 and r.json()["scope_hosts"] == ["acme.io", "beta.example"]
    assert bad.status_code == 422
    assert unknown.status_code == 404
