import os
import uuid
import pytest
import importlib
from fastapi.testclient import TestClient


DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("DATABASE_URL not set", allow_module_level=True)

os.environ.setdefault("STORAGE_PATH", f"/tmp/js-extractor-test-{uuid.uuid4()}")

from app.main import app
ingestion_module = importlib.import_module("app.api.routes.ingestion")


client = TestClient(app)


def test_ingestion_roundtrip():
    session_id = str(uuid.uuid4())
    content = "console.log('hello');"
    payload = {
        "metadata": {"sessionId": session_id},
        "files": [
            {
                "url": "https://example.com/app.js",
                "contentHash": "hash123",
                "sessionId": session_id,
                "capturedAt": "2026-02-06T00:00:00Z",
                "contentType": "application/javascript",
                "contentEncoding": "identity",
                "contentLength": len(content.encode("utf-8")),
                "content": content,
                "dependencies": [],
            }
        ],
    }

    response = client.post("/api/save-files", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["sessionId"] == session_id
    assert len(data["fileIds"]) == 1

    sessions = client.get("/api/sessions").json()
    assert any(s["id"] == session_id for s in sessions)

    files = client.get(f"/api/sessions/{session_id}/files").json()
    assert len(files) >= 1

    file_id = data["fileIds"][0]
    meta = client.get(f"/api/files/{file_id}").json()
    assert meta["url"] == "https://example.com/app.js"

    content_resp = client.get(f"/api/files/{file_id}/content")
    assert content_resp.status_code == 200
    assert "console.log" in content_resp.text

    deps = client.get(f"/api/files/{file_id}/dependencies").json()
    assert deps == []


def test_file_analysis_endpoint_returns_deduplicated_records():
    session_id = str(uuid.uuid4())
    content = """
    const token = 'sk_test_1234567890abcdefghijklmnop';
    fetch('/api/users');
    fetch('/api/users');
    """
    payload = {
        "metadata": {"sessionId": session_id},
        "files": [
            {
                "url": "https://example.com/app.js",
                "contentHash": "hash456",
                "sessionId": session_id,
                "capturedAt": "2026-02-06T00:00:00Z",
                "contentType": "application/javascript",
                "contentEncoding": "identity",
                "contentLength": len(content.encode("utf-8")),
                "content": content,
                "dependencies": [],
            }
        ],
    }

    ingest = client.post("/api/save-files", json=payload)
    assert ingest.status_code == 200
    file_id = ingest.json()["fileIds"][0]

    analyze = client.post(f"/api/files/{file_id}/analyze", json={"options": {"include_sourcemap": False}})
    assert analyze.status_code == 200
    data = analyze.json()
    assert data["success"] is True
    analysis = data["analysis"]["analysis"]
    assert isinstance(analysis["endpoints"], list)
    assert isinstance(analysis["secrets"], list)

    if analysis["endpoints"]:
        endpoint = analysis["endpoints"][0]
        assert "url" in endpoint
        assert "extractors" in endpoint
        assert "occurrenceCount" in endpoint
        assert "file" in endpoint

    if analysis["secrets"]:
        secret = analysis["secrets"][0]
        assert "value" in secret
        assert "extractors" in secret
        assert "occurrenceCount" in secret
        assert "file" in secret

    stored = client.get(f"/api/files/{file_id}/analysis")
    assert stored.status_code == 200
    assert stored.json()["status"] == "completed"


def test_ingestion_disable_analysis_metadata_bypasses_smart_triggers(monkeypatch):
    session_id = str(uuid.uuid4())
    content = "fetch('/api/broadcast'); const token='abc123';"
    analysis_called = {"count": 0}

    def force_trigger(self, content, file_metadata, sourcemap_status, manual_analysis_requested=False):
        return {"trigger": True, "reason": "forced", "criteria_met": ["forced"]}

    def fail_if_called(*args, **kwargs):
        analysis_called["count"] += 1
        raise AssertionError("run_ingestion_analysis should not run when disableAnalysis=true")

    monkeypatch.setattr(ingestion_module.SmartAnalysisTriggers, "should_trigger_analysis", force_trigger)
    monkeypatch.setattr(ingestion_module, "run_ingestion_analysis", fail_if_called)

    payload = {
        "metadata": {
            "sessionId": session_id,
            "performAnalysis": False,
            "disableAnalysis": True,
        },
        "files": [
            {
                "url": "https://wishandwash.co.il/assets/index-BDSyL5Fh.js",
                "contentHash": f"disable-analysis-{uuid.uuid4().hex}",
                "sessionId": session_id,
                "capturedAt": "2026-02-11T00:00:00Z",
                "contentType": "application/javascript",
                "contentEncoding": "identity",
                "contentLength": len(content.encode("utf-8")),
                "content": content,
                "dependencies": [],
            }
        ],
    }

    response = client.post("/api/save-files", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["analysis"]["status"] == "smart_skipped"
    assert analysis_called["count"] == 0
