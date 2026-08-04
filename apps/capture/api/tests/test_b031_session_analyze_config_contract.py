import os
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


@pytest.fixture(autouse=True)
def clear_session_analysis_jobs():
    with sessions_module.SESSION_ANALYSIS_LOCK:
        sessions_module.SESSION_ANALYSIS_JOBS.clear()
    yield
    with sessions_module.SESSION_ANALYSIS_LOCK:
        sessions_module.SESSION_ANALYSIS_JOBS.clear()


def upload_files(client: TestClient, session_id: str, count: int = 3) -> None:
    files = []
    for index in range(count):
        content = f"function b031_{index}() {{ return {index}; }}"
        files.append(
            {
                "url": f"https://wishandwash.co.il/assets/b031-{index}.js",
                "contentHash": f"b031-hash-{index}-{session_id[:8]}",
                "sessionId": session_id,
                "capturedAt": "2026-02-10T21:45:00Z",
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


class SuccessExtractor:
    def extract_all(self, _content, _metadata, options=None):
        return {
            "analysis": {"endpoints": [], "secrets": [], "dependencies": []},
            "stats": {"options": options or {}},
            "extractors_used": ["success-stub"],
        }


class FailFirstExtractor:
    def __init__(self):
        self.calls = 0

    def extract_all(self, _content, _metadata, options=None):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("forced first failure")
        return {
            "analysis": {"endpoints": [], "secrets": [], "dependencies": []},
            "stats": {"options": options or {}},
            "extractors_used": ["fail-first-stub"],
        }


class AlwaysFailExtractor:
    def extract_all(self, _content, _metadata, options=None):
        raise RuntimeError("forced failure")


def test_analyze_start_normalizes_options_and_applies_file_limit(monkeypatch):
    monkeypatch.setattr(sessions_module, "ComprehensiveExtractor", SuccessExtractor)

    client = TestClient(app)
    session_id = str(uuid.uuid4())
    upload_files(client, session_id, count=4)

    response = client.post(
        f"/api/sessions/{session_id}/analyze/start",
        json={
            "options": {
                "runMode": "advanced",
                "analysisType": "jsluice",
                "includeSourceMap": False,
                "resolveUrls": False,
                "continueOnError": False,
                "maxFilesToAnalyze": 2,
                "maxFailures": 1,
                "retryAttempts": 2,
            }
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["started"] is True

    job = payload["job"]
    options = job["options"]
    assert job["counts"]["total"] == 2
    assert options["run_mode"] == "advanced"
    assert options["analysis_type"] == "jsluice"
    assert options["include_sourcemap"] is False
    assert options["resolve_urls"] is False
    assert options["continue_on_error"] is False
    assert options["max_files_to_analyze"] == 2
    assert options["max_failures"] == 1
    assert options["retry_attempts"] == 2
    assert options["use_rep_endpoints"] is False
    assert options["use_rep_secrets"] is False
    assert options["use_jsluice_endpoints"] is True
    assert options["use_jsluice_secrets"] is True
    assert isinstance(options["submitted_at"], str) and len(options["submitted_at"]) > 8


def test_execute_session_analysis_fail_fast_cancels_remaining(monkeypatch):
    monkeypatch.setattr(sessions_module, "ComprehensiveExtractor", FailFirstExtractor)

    client = TestClient(app)
    session_id = str(uuid.uuid4())
    upload_files(client, session_id, count=5)

    response = client.post(
        f"/api/sessions/{session_id}/analyze",
        json={"options": {"continueOnError": False}},
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["sessionId"] == session_id
    assert payload["success"] is False
    assert payload["failed"] == 1
    assert payload["analyzed"] == 0
    assert payload["cancelledFiles"] == 4
    assert len(payload["failures"]) == 1


def test_execute_session_analysis_max_failures_cancels_remaining(monkeypatch):
    monkeypatch.setattr(sessions_module, "ComprehensiveExtractor", AlwaysFailExtractor)

    client = TestClient(app)
    session_id = str(uuid.uuid4())
    upload_files(client, session_id, count=6)

    response = client.post(
        f"/api/sessions/{session_id}/analyze",
        json={"options": {"continueOnError": True, "maxFailures": 2}},
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["sessionId"] == session_id
    assert payload["success"] is False
    assert payload["failed"] == 2
    assert payload["analyzed"] == 0
    assert payload["cancelledFiles"] == 4
    assert len(payload["failures"]) == 2
