"""DB-route tests for the projects CRUD API. Real Postgres required (run in the
api container). Self-skips if DATABASE_URL is unset."""
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


def test_create_project_fills_system_defaults():
    r = client.post("/api/projects", json={"name": "acme-bounty"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "acme-bounty"
    assert body["defaults"]["scope"]["includeSubdomains"] is True
    assert body["defaults"]["capture"]["maxAssetMb"] == 10
    assert body["defaults"]["capture"]["outOfScopeMode"] == "tag"


def test_create_project_rejects_empty_name():
    r = client.post("/api/projects", json={"name": "   "})
    assert r.status_code == 400


def test_create_project_rejects_bad_config():
    r = client.post("/api/projects", json={"name": "bad", "defaults": {"capture": {"maxAssetMb": 25}}})
    assert r.status_code == 400


def test_get_and_list_project():
    pid = client.post("/api/projects", json={"name": "list-me"}).json()["id"]
    assert client.get(f"/api/projects/{pid}").status_code == 200
    ids = {p["id"] for p in client.get("/api/projects").json()}
    assert pid in ids


def test_get_unknown_project_404():
    assert client.get(f"/api/projects/{uuid.uuid4()}").status_code == 404


def test_patch_deep_merges_defaults():
    pid = client.post("/api/projects", json={"name": "patch-me"}).json()["id"]
    r = client.patch(f"/api/projects/{pid}", json={"defaults": {"analysis": {"analyzeOnUpload": True}}})
    assert r.status_code == 200, r.text
    defaults = r.json()["defaults"]
    assert defaults["analysis"]["analyzeOnUpload"] is True
    assert defaults["analysis"]["captureSourceMaps"] is True  # untouched sibling preserved


def test_delete_project():
    pid = client.post("/api/projects", json={"name": "del-me"}).json()["id"]
    assert client.delete(f"/api/projects/{pid}").status_code == 200
    assert client.get(f"/api/projects/{pid}").status_code == 404
