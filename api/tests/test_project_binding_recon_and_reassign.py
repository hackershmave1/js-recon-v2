"""DB-route tests for engagement binding (full-binding feature):
 - recon start binds the created session to a project (unknown/stale -> standalone,
   never aborts the crawl);
 - PATCH /api/sessions/{id} (re)assigns, unassigns (explicit null), leaves an absent
   field untouched, and 404s on an unknown project.
Real Postgres (host lane; skipped without DATABASE_URL)."""
import os
import uuid

import pytest
from fastapi.testclient import TestClient

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("DATABASE_URL not set", allow_module_level=True)

os.environ.setdefault("STORAGE_PATH", f"/tmp/js-extractor-test-{uuid.uuid4()}")

from app.main import app
from app.api.routes import recon

client = TestClient(app)


@pytest.fixture
def no_crawl(monkeypatch):
    """Neutralize the background crawl: the worker becomes a no-op so start_recon_job
    persists the session/job rows synchronously without launching katana/headless.
    Patches only the worker function (not threading.Thread), so nothing interferes with
    the anyio threadpool TestClient uses to run the sync endpoint."""
    monkeypatch.setattr(recon, "run_recon_job_worker", lambda *a, **k: None)


def _make_project(name):
    resp = client.post("/api/projects", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _get_session(session_id):
    rows = client.get("/api/sessions").json()
    match = [s for s in rows if s["id"] == session_id]
    assert match, f"session {session_id} not found"
    return match[0]


def _save(session_id, content_hash):
    """Create a loose (project-less) session via a single-file capture."""
    content = f"function s_{content_hash}() {{ return 1; }}"
    resp = client.post("/api/save-files", json={
        "metadata": {"sessionId": session_id},
        "files": [{
            "url": f"https://acme.com/{content_hash}.js", "contentHash": content_hash,
            "sessionId": session_id, "contentType": "application/javascript",
            "contentEncoding": "identity", "contentLength": len(content),
            "content": content, "dependencies": [],
        }],
    })
    assert resp.status_code == 200, resp.text


def _start_recon(sid, project_id=..., url="https://wishandwash.co.il"):
    body = {"sessionId": sid, "url": url}
    if project_id is not ...:
        body["projectId"] = project_id
    return client.post("/api/recon/jobs/start", json=body)


# ---- recon forward binding ------------------------------------------------

def test_recon_start_binds_session_to_project(no_crawl):
    pid = _make_project("recon-bind")
    sid = str(uuid.uuid4())
    r = _start_recon(sid, project_id=pid)
    assert r.status_code == 200, r.text
    assert r.json()["sessionCreated"] is True
    assert _get_session(sid)["projectId"] == pid


def test_recon_start_unknown_project_coerced_to_standalone(no_crawl):
    sid = str(uuid.uuid4())
    ghost = str(uuid.uuid4())  # valid uuid, never created
    r = _start_recon(sid, project_id=ghost)
    assert r.status_code == 200, r.text  # a stale project ref must not abort the crawl
    assert _get_session(sid)["projectId"] is None


def test_recon_start_without_project_is_standalone(no_crawl):
    sid = str(uuid.uuid4())
    r = _start_recon(sid)
    assert r.status_code == 200, r.text
    assert _get_session(sid)["projectId"] is None


# ---- session (re)assignment via PATCH -------------------------------------

def test_patch_assigns_loose_session_to_project():
    pid = _make_project("assign-target")
    sid = str(uuid.uuid4())
    _save(sid, "reassign-1")
    assert _get_session(sid)["projectId"] is None
    r = client.patch(f"/api/sessions/{sid}", json={"projectId": pid})
    assert r.status_code == 200, r.text
    assert r.json()["projectId"] == pid
    assert _get_session(sid)["projectId"] == pid


def test_patch_unassigns_with_explicit_null():
    pid = _make_project("unassign-src")
    sid = str(uuid.uuid4())
    _save(sid, "unassign-1")
    client.patch(f"/api/sessions/{sid}", json={"projectId": pid})
    r = client.patch(f"/api/sessions/{sid}", json={"projectId": None})
    assert r.status_code == 200, r.text
    assert r.json()["projectId"] is None
    assert _get_session(sid)["projectId"] is None


def test_patch_absent_project_leaves_binding_untouched():
    pid = _make_project("untouched-src")
    sid = str(uuid.uuid4())
    _save(sid, "untouched-1")
    client.patch(f"/api/sessions/{sid}", json={"projectId": pid})
    # A name-only PATCH must not clear the existing engagement binding.
    r = client.patch(f"/api/sessions/{sid}", json={"name": "renamed"})
    assert r.status_code == 200, r.text
    assert r.json()["projectId"] == pid


def test_patch_unknown_project_is_404_and_leaves_binding():
    pid = _make_project("keep-src")
    sid = str(uuid.uuid4())
    _save(sid, "keep-1")
    client.patch(f"/api/sessions/{sid}", json={"projectId": pid})
    ghost = str(uuid.uuid4())
    r = client.patch(f"/api/sessions/{sid}", json={"projectId": ghost})
    assert r.status_code == 404
    assert _get_session(sid)["projectId"] == pid  # unchanged
