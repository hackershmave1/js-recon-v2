import os
import uuid

import pytest
from fastapi.testclient import TestClient


DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("DATABASE_URL not set", allow_module_level=True)

os.environ.setdefault("STORAGE_PATH", f"/tmp/js-extractor-test-{uuid.uuid4()}")

from app.main import app


def _upload_single_file(client: TestClient, session_id: str) -> str:
    content = "function t035(){ return '/api/t035'; } t035();"
    payload = {
        "metadata": {
            "sessionId": session_id,
            "performAnalysis": False,
        },
        "files": [
            {
                "url": "https://wishandwash.co.il/t035.js",
                "contentHash": f"t035-hash-{session_id[:8]}",
                "sessionId": session_id,
                "capturedAt": "2026-02-09T16:00:00Z",
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
    file_ids = response.json().get("fileIds") or []
    assert file_ids
    return file_ids[0]


def _find_session_row(client: TestClient, session_id: str) -> dict:
    response = client.get("/api/sessions")
    assert response.status_code == 200
    rows = response.json()
    for row in rows:
        if row.get("id") == session_id:
            return row
    raise AssertionError(f"Session {session_id} not found")


def test_sessions_endpoint_exposes_analysis_summary():
    client = TestClient(app)
    session_id = str(uuid.uuid4())
    file_id = _upload_single_file(client, session_id)

    row_before = _find_session_row(client, session_id)
    summary_before = row_before.get("analysisSummary") or {}
    assert summary_before.get("performed") is False
    assert int(summary_before.get("completed") or 0) == 0

    analyze_response = client.post(f"/api/files/{file_id}/analyze", json={"options": {}})
    assert analyze_response.status_code == 200

    row_after = _find_session_row(client, session_id)
    summary_after = row_after.get("analysisSummary") or {}
    assert summary_after.get("performed") is True
    assert int(summary_after.get("completed") or 0) >= 1
