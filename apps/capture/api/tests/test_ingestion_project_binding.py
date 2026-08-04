"""DB-route tests: save-files binds project + config snapshot on create only, and
GET /api/sessions exposes the provenance fields. Real Postgres (run in container)."""
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


def _save(session_id, content_hash, *, project_id=None, capture_config=None, override_keys=None):
    content = f"function b_{content_hash}() {{ return 1; }}"
    meta = {"sessionId": session_id}
    if project_id is not None:
        meta["projectId"] = project_id
    if capture_config is not None:
        meta["captureConfig"] = capture_config
    if override_keys is not None:
        meta["overrideKeys"] = override_keys
    resp = client.post("/api/save-files", json={
        "metadata": meta,
        "files": [{
            "url": f"https://acme.com/{content_hash}.js", "contentHash": content_hash,
            "sessionId": session_id, "contentType": "application/javascript",
            "contentEncoding": "identity", "contentLength": len(content),
            "content": content, "dependencies": [],
        }],
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


def _get_session(session_id):
    rows = client.get("/api/sessions").json()
    match = [s for s in rows if s["id"] == session_id]
    assert match, f"session {session_id} not found"
    return match[0]


def test_save_files_binds_project_and_config_on_create():
    pid = client.post("/api/projects", json={"name": "bind"}).json()["id"]
    sid = str(uuid.uuid4())
    _save(sid, "bind-1", project_id=pid,
          capture_config={"analysis": {"analyzeOnUpload": True, "captureSourceMaps": False}},
          override_keys=["analysis.analyzeOnUpload"])
    s = _get_session(sid)
    assert s["projectId"] == pid
    assert s["overrideKeys"] == ["analysis.analyzeOnUpload"]
    assert s["captureConfig"]["analysis"]["analyzeOnUpload"] is True


def test_save_files_does_not_rebind_on_append():
    pid = client.post("/api/projects", json={"name": "norebind"}).json()["id"]
    sid = str(uuid.uuid4())
    _save(sid, "nr-1")            # create loose (no project)
    _save(sid, "nr-2", project_id=pid)  # append attempts to bind -> must be ignored
    assert _get_session(sid)["projectId"] is None


def test_save_files_rejects_bad_capture_config():
    sid = str(uuid.uuid4())
    resp = client.post("/api/save-files", json={
        "metadata": {"sessionId": sid, "captureConfig": {"capture": {"maxAssetMb": 99}}},
        "files": [{
            "url": "https://acme.com/x.js", "contentHash": "bad-cc", "sessionId": sid,
            "contentType": "application/javascript", "contentEncoding": "identity",
            "contentLength": 10, "content": "var a = 1;", "dependencies": [],
        }],
    })
    assert resp.status_code == 400


def test_delete_project_leaves_session_loose():
    pid = client.post("/api/projects", json={"name": "del-loose"}).json()["id"]
    sid = str(uuid.uuid4())
    _save(sid, "dl-1", project_id=pid)
    assert client.delete(f"/api/projects/{pid}").status_code == 200
    assert _get_session(sid)["projectId"] is None


def test_save_files_unknown_project_coerced_to_standalone():
    sid = str(uuid.uuid4())
    ghost = str(uuid.uuid4())  # never created
    resp = _save(sid, "ghost-1", project_id=ghost,
                 capture_config={"analysis": {"analyzeOnUpload": True, "captureSourceMaps": False}},
                 override_keys=["analysis.analyzeOnUpload"])
    assert resp["success"] is True          # capture saved, not dropped
    s = _get_session(sid)
    assert s["projectId"] is None           # unknown project -> standalone
    assert s["captureConfig"]["analysis"]["analyzeOnUpload"] is True  # resolved config preserved
