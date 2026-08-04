import os
import uuid

import pytest
from fastapi.testclient import TestClient


DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("DATABASE_URL not set", allow_module_level=True)

os.environ.setdefault("STORAGE_PATH", f"/tmp/js-extractor-test-{uuid.uuid4()}")

from app.main import app


def upload_one_file(client: TestClient, session_id: str, content_hash: str) -> str:
    content = f"function t022_{content_hash}() {{ return true; }}"
    payload = {
        "metadata": {"sessionId": session_id},
        "files": [
            {
                "url": f"https://example.com/{content_hash}.js",
                "contentHash": content_hash,
                "sessionId": session_id,
                "capturedAt": "2026-02-09T10:00:00Z",
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
    return response.json()["fileIds"][0]


def test_bulk_delete_files_partial_failure():
    client = TestClient(app)
    session_id = str(uuid.uuid4())
    file_id = upload_one_file(client, session_id, "t022-file-1")

    response = client.post(
        "/api/files/bulk-delete",
        json={"fileIds": [file_id, "not-a-uuid"]},
    )
    assert response.status_code == 200
    body = response.json()

    assert body["success"] is False
    assert body["requested"] == 2
    assert file_id in body["deleted"]
    assert len(body["failed"]) == 1
    assert body["failed"][0]["fileId"] == "not-a-uuid"


def test_bulk_delete_sessions_partial_failure():
    client = TestClient(app)
    session_a = str(uuid.uuid4())
    session_b = str(uuid.uuid4())
    upload_one_file(client, session_a, "t022-session-a")
    upload_one_file(client, session_b, "t022-session-b")

    response = client.post(
        "/api/sessions/bulk-delete",
        json={"sessionIds": [session_a, "bad-session-id"]},
    )
    assert response.status_code == 200
    body = response.json()

    assert body["success"] is False
    assert body["requested"] == 2
    assert session_a in body["deleted"]
    assert len(body["failed"]) == 1
    assert body["failed"][0]["sessionId"] == "bad-session-id"

    # Ensure undeleted valid session still exists.
    sessions_response = client.get("/api/sessions")
    assert sessions_response.status_code == 200
    remaining_ids = {session["id"] for session in sessions_response.json()}
    assert session_b in remaining_ids
