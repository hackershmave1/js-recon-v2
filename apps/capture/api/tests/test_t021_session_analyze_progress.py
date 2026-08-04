import os
import time
import uuid
import importlib

import pytest
from fastapi.testclient import TestClient


DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("DATABASE_URL not set", allow_module_level=True)

os.environ.setdefault("STORAGE_PATH", f"/tmp/js-extractor-test-{uuid.uuid4()}")

from app.main import app
sessions_module = importlib.import_module("app.api.routes.sessions")


def upload_files(client: TestClient, session_id: str, count: int = 2) -> None:
    files = []
    for index in range(count):
        content = f"function t021_{index}() {{ return {index}; }}"
        files.append(
            {
                "url": f"https://example.com/t021-{index}.js",
                "contentHash": f"t021-hash-{index}-{session_id[:8]}",
                "sessionId": session_id,
                "capturedAt": "2026-02-09T10:00:00Z",
                "contentType": "application/javascript",
                "contentEncoding": "identity",
                "contentLength": len(content.encode("utf-8")),
                "content": content,
                "dependencies": [],
            }
        )

    payload = {"metadata": {"sessionId": session_id}, "files": files}
    response = client.post("/api/save-files", json=payload)
    assert response.status_code == 200


class StubExtractor:
    def extract_all(self, _content, _metadata, options=None):
        time.sleep(0.2)
        return {
            "analysis": {"endpoints": [], "secrets": [], "dependencies": []},
            "stats": {"options": options or {}},
            "extractors_used": ["stub"],
        }


class SlowStubExtractor:
    def extract_all(self, _content, _metadata, options=None):
        time.sleep(0.4)
        return {
            "analysis": {"endpoints": [], "secrets": [], "dependencies": []},
            "stats": {"options": options or {}},
            "extractors_used": ["slow-stub"],
        }


def wait_for_completion(client: TestClient, session_id: str, timeout_seconds: float = 8.0) -> dict:
    deadline = time.time() + timeout_seconds
    latest = None
    while time.time() < deadline:
        response = client.get(f"/api/sessions/{session_id}/analyze/progress")
        assert response.status_code == 200
        latest = response.json()
        status = str(latest["job"]["jobStatus"]).lower()
        if status in {"completed", "failed", "idle", "cancelled"}:
            return latest
        time.sleep(0.2)
    raise AssertionError(f"Timed out waiting for session {session_id} analysis completion. Last={latest}")


def test_session_analysis_start_and_progress(monkeypatch):
    monkeypatch.setattr(sessions_module, "ComprehensiveExtractor", StubExtractor)
    client = TestClient(app)
    session_id = str(uuid.uuid4())
    upload_files(client, session_id, count=2)

    start = client.post(
        f"/api/sessions/{session_id}/analyze/start",
        json={"options": {"resolveUrls": True}},
    )
    assert start.status_code == 200
    start_data = start.json()
    assert start_data["success"] is True
    assert start_data["started"] is True
    assert start_data["job"]["counts"]["total"] == 2

    progress = client.get(f"/api/sessions/{session_id}/analyze/progress")
    assert progress.status_code == 200
    progress_data = progress.json()
    assert progress_data["job"]["counts"]["total"] == 2
    assert str(progress_data["job"]["jobStatus"]).lower() in {"queued", "running", "completed"}

    completed = wait_for_completion(client, session_id)
    job = completed["job"]
    assert str(job["jobStatus"]).lower() in {"completed", "idle"}
    assert job["counts"]["total"] == 2
    assert job["summary"]["analyzed"] >= 1
    assert (job["counts"]["completed"] + job["counts"]["failed"]) == job["counts"]["total"]


def test_session_analysis_start_returns_running_state_when_already_active(monkeypatch):
    monkeypatch.setattr(sessions_module, "ComprehensiveExtractor", StubExtractor)
    client = TestClient(app)
    session_id = str(uuid.uuid4())
    upload_files(client, session_id, count=3)

    first = client.post(f"/api/sessions/{session_id}/analyze/start", json={"options": {}})
    assert first.status_code == 200
    assert first.json()["started"] is True

    second = client.post(f"/api/sessions/{session_id}/analyze/start", json={"options": {}})
    assert second.status_code == 200
    second_data = second.json()
    assert second_data["success"] is True
    assert second_data["started"] is False
    assert "already running" in second_data["message"].lower()
    assert str(second_data["job"]["jobStatus"]).lower() in {"queued", "running"}

    wait_for_completion(client, session_id)


def test_session_analysis_stop_cancels_running_job(monkeypatch):
    monkeypatch.setattr(sessions_module, "ComprehensiveExtractor", SlowStubExtractor)
    client = TestClient(app)
    session_id = str(uuid.uuid4())
    upload_files(client, session_id, count=8)

    start = client.post(f"/api/sessions/{session_id}/analyze/start", json={"options": {}})
    assert start.status_code == 200
    assert start.json()["started"] is True

    time.sleep(0.15)
    stop = client.post(f"/api/sessions/{session_id}/analyze/stop")
    assert stop.status_code == 200
    stop_data = stop.json()
    assert stop_data["success"] is True
    assert stop_data["stopRequested"] is True

    completed = wait_for_completion(client, session_id, timeout_seconds=12.0)
    job = completed["job"]
    assert str(job["jobStatus"]).lower() == "cancelled"
    assert bool(job.get("cancelRequested")) is True
    assert (job["counts"].get("cancelled") or 0) >= 1
    assert (job["summary"].get("cancelled") or 0) >= 1
