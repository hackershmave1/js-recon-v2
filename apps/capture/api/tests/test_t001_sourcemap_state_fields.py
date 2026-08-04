import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect as sqlalchemy_inspect

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("DATABASE_URL not set", allow_module_level=True)

os.environ.setdefault("STORAGE_PATH", f"/tmp/js-extractor-t001-{uuid.uuid4()}")

from app.main import app
from app.db import SessionLocal, engine
from app.models import SourceMap as DbSourceMap


client = TestClient(app)


def test_t001_source_map_table_has_processing_state_columns():
    inspector = sqlalchemy_inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("source_maps")}

    assert "detected_map_url" in columns
    assert "processing_status" in columns
    assert "processing_error" in columns
    assert "reconstructed_files_count" in columns
    assert "processed_at" in columns


def test_t001_ingestion_creates_sourcemap_row_with_expected_defaults():
    session_id = str(uuid.uuid4())
    content = "console.log('t001');"
    payload = {
        "metadata": {"sessionId": session_id},
        "files": [
            {
                "url": "https://example.com/t001.js",
                "contentHash": f"t001-{session_id[:8]}",
                "sessionId": session_id,
                "capturedAt": "2026-02-08T00:00:00Z",
                "contentType": "application/javascript",
                "contentEncoding": "identity",
                "contentLength": len(content.encode("utf-8")),
                "content": content,
                "sourceMapUrl": "https://example.com/t001.js.map",
                "dependencies": [],
            }
        ],
    }

    response = client.post("/api/save-files", json=payload)
    assert response.status_code == 200

    file_id = uuid.UUID(response.json()["fileIds"][0])

    db = SessionLocal()
    try:
        row = db.query(DbSourceMap).filter(DbSourceMap.file_id == file_id).first()
        assert row is not None
        assert row.map_url == "https://example.com/t001.js.map"
        assert row.detected_map_url is None
        assert row.processing_status == "pending"
        assert row.processing_error is None
        assert row.reconstructed_files_count == 0
        assert row.processed_at is None
    finally:
        db.close()
