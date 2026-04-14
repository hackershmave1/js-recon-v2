from datetime import datetime
import uuid

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import File as DbFile


def _upload_file_with_sourcemap(client: TestClient, session_id: str) -> str:
    payload = {
        "metadata": {"sessionId": session_id},
        "files": [
            {
                "url": "https://wishandwash.co.il/static/js/app.min.js",
                "contentHash": "t016-purge-hash",
                "sessionId": session_id,
                "capturedAt": "2026-02-09T00:00:00Z",
                "contentType": "application/javascript",
                "contentEncoding": "identity",
                "contentLength": 43,
                "content": "console.log('wishandwash');//# sourceMappingURL=app.min.js.map",
                "sourceMapUrl": "https://wishandwash.co.il/static/js/app.min.js.map",
                "sourceMapContent": {
                    "version": 3,
                    "file": "app.min.js",
                    "sources": ["src/app.js"],
                    "names": [],
                    "mappings": "AAAA",
                    "sourcesContent": ["console.log('wishandwash');"],
                },
                "dependencies": [],
            }
        ],
    }
    response = client.post("/api/save-files", json=payload)
    assert response.status_code == 200
    data = response.json()
    return data["fileIds"][0]


def test_purged_content_endpoints_return_410():
    client = TestClient(app)
    session_id = str(uuid.uuid4())
    file_id = _upload_file_with_sourcemap(client, session_id)

    db = SessionLocal()
    try:
        file_row = db.query(DbFile).filter(DbFile.id == uuid.UUID(file_id)).first()
        assert file_row is not None
        assert file_row.source_map is not None

        now = datetime.utcnow()
        file_row.content_purged = True
        file_row.content_purged_at = now
        file_row.purge_reason = "retention_ttl_expired"
        file_row.source_map.content_purged = True
        file_row.source_map.content_purged_at = now
        file_row.source_map.purge_reason = "retention_ttl_expired"
        db.commit()
    finally:
        db.close()

    metadata_response = client.get(f"/api/files/{file_id}")
    assert metadata_response.status_code == 200
    metadata = metadata_response.json()
    assert metadata["contentPurged"] is True
    assert metadata["purgeReason"] == "retention_ttl_expired"
    assert metadata["sourceMap"]["contentPurged"] is True

    content_response = client.get(f"/api/files/{file_id}/content")
    assert content_response.status_code == 410
    content_detail = content_response.json()["detail"]
    assert content_detail["artifactType"] == "file_content"
    assert content_detail["contentPurged"] is True
    assert content_detail["purgeReason"] == "retention_ttl_expired"

    sourcemap_response = client.get(f"/api/files/{file_id}/sourcemap-content")
    assert sourcemap_response.status_code == 410
    sourcemap_detail = sourcemap_response.json()["detail"]
    assert sourcemap_detail["artifactType"] == "sourcemap_content"
    assert sourcemap_detail["contentPurged"] is True
    assert sourcemap_detail["purgeReason"] == "retention_ttl_expired"

    reconstructed_response = client.get(f"/api/files/{file_id}/reconstructed-sources")
    assert reconstructed_response.status_code == 410
    reconstructed_detail = reconstructed_response.json()["detail"]
    assert reconstructed_detail["artifactType"] == "sourcemap_content"
