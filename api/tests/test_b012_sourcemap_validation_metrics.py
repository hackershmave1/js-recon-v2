import os
import uuid

import pytest
from fastapi.testclient import TestClient


DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("DATABASE_URL not set", allow_module_level=True)

os.environ.setdefault("STORAGE_PATH", f"/tmp/js-extractor-test-{uuid.uuid4()}")

from app.main import app


def _upload_file_with_sourcemap(
    client: TestClient,
    session_id: str,
    *,
    url: str,
    content: str,
    source_map_url: str | None = None,
    source_map_content: dict | None = None,
) -> dict:
    payload = {
        "metadata": {
            "sessionId": session_id,
            "performAnalysis": False,
            "disableAnalysis": True,
        },
        "files": [
            {
                "url": url,
                "contentHash": f"b012-{uuid.uuid4().hex}",
                "sessionId": session_id,
                "capturedAt": "2026-02-12T12:00:00Z",
                "contentType": "application/javascript",
                "contentEncoding": "identity",
                "contentLength": len(content.encode("utf-8")),
                "content": content,
                "sourceMapUrl": source_map_url,
                "sourceMapContent": source_map_content,
                "dependencies": [],
            }
        ],
    }
    response = client.post("/api/save-files", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    return body


def test_sourcemap_validation_summary_endpoint_reports_denominators_and_failures():
    client = TestClient(app)
    session_id = str(uuid.uuid4())

    valid_js = "console.log('wishandwash');//# sourceMappingURL=app.min.js.map"
    valid_map = {
        "version": 3,
        "sources": ["src/app.ts"],
        "sourcesContent": ["export const run = () => true;"],
        "mappings": "",
        "names": [],
    }
    _upload_file_with_sourcemap(
        client,
        session_id,
        url="https://wishandwash.co.il/assets/app.min.js",
        content=valid_js,
        source_map_url="https://wishandwash.co.il/assets/app.min.js.map",
        source_map_content=valid_map,
    )

    invalid_js = "console.log('broken map');//# sourceMappingURL=broken.js.map"
    _upload_file_with_sourcemap(
        client,
        session_id,
        url="https://wishandwash.co.il/assets/broken.js",
        content=invalid_js,
        source_map_url="https://wishandwash.co.il/assets/broken.js.map",
        source_map_content={"not": "a valid sourcemap format"},
    )

    resp = client.get(f"/api/sessions/{session_id}/sourcemap-validation")
    assert resp.status_code == 200
    body = resp.json()

    assert body["sessionId"] == session_id
    assert body["dedupe"] is True
    assert len(body["files"]) == 2

    summary = body["summary"]
    denominators = summary["denominators"]
    counts = summary["counts"]
    reasons = summary["failure_reasons"]

    assert denominators["total_js"] == 2
    assert denominators["map_candidates"] == 2
    assert denominators["map_fetched"] == 2
    assert counts["processed"] >= 1
    assert counts["failed"] >= 1
    assert reasons.get("decode_invalid_json", 0) >= 1

    files_with_validation = [f for f in body["files"] if isinstance(f.get("validation"), dict)]
    assert len(files_with_validation) == 2
    for row in files_with_validation:
        validation = row["validation"]
        assert set(
            [
                "detected",
                "fetched",
                "http_status",
                "content_type",
                "json_valid",
                "processed",
                "candidate_source",
                "selected_candidate",
                "failure_class",
                "updated_at",
            ]
        ).issubset(validation.keys())


def test_session_files_endpoint_exposes_sourcemap_validation_object():
    client = TestClient(app)
    session_id = str(uuid.uuid4())
    js_content = "console.log('coverage');//# sourceMappingURL=index.js.map"
    source_map = {
        "version": 3,
        "sources": ["src/index.ts"],
        "sourcesContent": ["console.log('ok')"],
        "mappings": "",
        "names": [],
    }
    _upload_file_with_sourcemap(
        client,
        session_id,
        url="https://wishandwash.co.il/assets/index.js",
        content=js_content,
        source_map_url="https://wishandwash.co.il/assets/index.js.map",
        source_map_content=source_map,
    )

    files_resp = client.get(f"/api/sessions/{session_id}/files?dedupe=true")
    assert files_resp.status_code == 200
    rows = files_resp.json()
    assert rows
    source_map_payload = rows[0].get("sourceMap")
    assert source_map_payload is not None
    validation = source_map_payload.get("validation")
    assert isinstance(validation, dict)
    assert validation.get("detected") is True
    assert validation.get("processed") is True
