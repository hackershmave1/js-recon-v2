"""DB-route tests: PATCH edits a session's capture_config override + override_keys.
Real Postgres (run in container)."""
import os
import uuid

import pytest
from fastapi.testclient import TestClient

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("DATABASE_URL not set", allow_module_level=True)

os.environ.setdefault("STORAGE_PATH", f"/tmp/js-extractor-test-{uuid.uuid4()}")

from app.main import app

client = TestClient(app)


def _save(session_id, content_hash):
    content = "function p6(){ return 1; }"
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


def _get_session(session_id):
    rows = client.get("/api/sessions").json()
    return next(s for s in rows if s["id"] == session_id)


def test_patch_updates_capture_config_and_override_keys():
    sid = str(uuid.uuid4())
    _save(sid, "p6-1")
    r = client.patch(f"/api/sessions/{sid}", json={
        "captureConfig": {"capture": {"outOfScopeMode": "exclude", "maxAssetMb": 5}},
        "overrideKeys": ["capture.outOfScopeMode", "capture.maxAssetMb"],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["captureConfig"]["capture"]["outOfScopeMode"] == "exclude"
    assert set(body["overrideKeys"]) == {"capture.outOfScopeMode", "capture.maxAssetMb"}
    s = _get_session(sid)
    assert s["captureConfig"]["capture"]["maxAssetMb"] == 5


def test_patch_rejects_bad_capture_config():
    sid = str(uuid.uuid4())
    _save(sid, "p6-2")
    r = client.patch(f"/api/sessions/{sid}", json={"captureConfig": {"capture": {"maxAssetMb": 99}}})
    assert r.status_code == 400


def test_patch_scope_still_works():
    sid = str(uuid.uuid4())
    _save(sid, "p6-3")
    r = client.patch(f"/api/sessions/{sid}", json={"rootDomains": ["app.acme.com"], "includeSubdomains": False})
    assert r.status_code == 200, r.text
    assert r.json()["rootDomains"] == ["app.acme.com"]
    assert r.json()["includeSubdomains"] is False
