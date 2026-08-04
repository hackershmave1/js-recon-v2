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


def test_ingestion_skips_analysis_when_not_requested():
    session_id = str(uuid.uuid4())
    content = "fetch('/api/users');"
    payload = {
        "metadata": {"sessionId": session_id, "performAnalysis": False},
        "files": [
            {
                "url": "https://example.com/t008-skip.js",
                "contentHash": "t008skiphash",
                "sessionId": session_id,
                "capturedAt": "2026-02-08T22:00:00Z",
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
    body = response.json()
    file_id = body["fileIds"][0]

    assert body["analysis"]["requested"] is False
    assert body["analysis"]["status"] == "skipped"
    assert body["analysis"]["completed"] == 0
    assert body["analysis"]["failed"] == 0
    assert body["files"][0]["analysis"]["status"] == "skipped"

    analysis_response = client.get(f"/api/files/{file_id}/analysis")
    assert analysis_response.status_code == 404


def test_ingestion_runs_analysis_when_requested():
    session_id = str(uuid.uuid4())
    content = """
    const token = "sk_test_1234567890abcdefghijklmnop";
    fetch("/api/orders");
    """
    payload = {
        "metadata": {
            "sessionId": session_id,
            "performAnalysis": True,
            "analysisOptions": {"include_sourcemap": False},
        },
        "files": [
            {
                "url": "https://example.com/t008-run.js",
                "contentHash": "t008runhash",
                "sessionId": session_id,
                "capturedAt": "2026-02-08T22:00:01Z",
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
    body = response.json()
    file_id = body["fileIds"][0]

    assert body["analysis"]["requested"] is True
    assert body["analysis"]["completed"] + body["analysis"]["failed"] == 1
    assert body["analysis"]["status"] in {"completed", "partial_failed", "failed"}
    assert body["files"][0]["analysis"]["status"] in {"completed", "failed"}

    analysis_response = client.get(f"/api/files/{file_id}/analysis")
    assert analysis_response.status_code == 200
    analysis_body = analysis_response.json()
    assert analysis_body["status"] in {"completed", "failed"}
