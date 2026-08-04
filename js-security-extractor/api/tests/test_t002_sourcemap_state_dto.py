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


def test_get_file_includes_sourcemap_state():
    session_id = str(uuid.uuid4())
    js_content = "console.log('with sourcemap');"
    map_url = "https://example.com/static/app.js.map"
    payload = {
        "metadata": {"sessionId": session_id},
        "files": [
            {
                "url": "https://example.com/static/app.js",
                "contentHash": "t002hash1",
                "sessionId": session_id,
                "capturedAt": "2026-02-08T21:50:00Z",
                "contentType": "application/javascript",
                "contentEncoding": "identity",
                "contentLength": len(js_content.encode("utf-8")),
                "content": js_content,
                "sourceMapUrl": map_url,
                "dependencies": [],
            }
        ],
    }

    ingest = client.post("/api/save-files", json=payload)
    assert ingest.status_code == 200
    file_id = ingest.json()["fileIds"][0]

    response = client.get(f"/api/files/{file_id}")
    assert response.status_code == 200
    body = response.json()

    assert "sourceMap" in body
    source_map = body["sourceMap"]
    assert source_map is not None
    assert source_map["fileId"] == file_id
    assert source_map["mapUrl"] == map_url
    assert source_map["detectedMapUrl"] == map_url
    assert isinstance(source_map["parsed"], bool)
    assert source_map["processingStatus"] in {"pending", "processing", "completed", "completed_limited", "failed"}
    assert source_map["reconstructedFilesCount"] >= 0
    assert "processingError" in source_map
    assert "processedAt" in source_map


def test_list_session_files_includes_sourcemap_state():
    session_id = str(uuid.uuid4())
    js_content = "console.log('js file');"
    html_content = "<html><body>no sourcemap</body></html>"
    map_url = "https://example.com/assets/main.js.map"

    payload = {
        "metadata": {"sessionId": session_id},
        "files": [
            {
                "url": "https://example.com/assets/main.js",
                "contentHash": "t002hash2",
                "sessionId": session_id,
                "capturedAt": "2026-02-08T21:51:00Z",
                "contentType": "application/javascript",
                "contentEncoding": "identity",
                "contentLength": len(js_content.encode("utf-8")),
                "content": js_content,
                "sourceMapUrl": map_url,
                "dependencies": [],
            },
            {
                "url": "https://example.com/index.html",
                "contentHash": "t002hash3",
                "sessionId": session_id,
                "capturedAt": "2026-02-08T21:52:00Z",
                "contentType": "text/html",
                "contentEncoding": "identity",
                "contentLength": len(html_content.encode("utf-8")),
                "content": html_content,
                "dependencies": [],
            },
        ],
    }

    ingest = client.post("/api/save-files", json=payload)
    assert ingest.status_code == 200

    response = client.get(f"/api/sessions/{session_id}/files")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) >= 2

    by_url = {row["url"]: row for row in rows}
    assert "https://example.com/assets/main.js" in by_url
    assert "https://example.com/index.html" in by_url

    js_row = by_url["https://example.com/assets/main.js"]
    assert "sourceMap" in js_row
    assert js_row["sourceMap"] is not None
    assert js_row["sourceMap"]["mapUrl"] == map_url
    assert js_row["sourceMap"]["processingStatus"] in {"pending", "processing", "completed", "completed_limited", "failed"}

    html_row = by_url["https://example.com/index.html"]
    assert "sourceMap" in html_row
    assert html_row["sourceMap"] is None
