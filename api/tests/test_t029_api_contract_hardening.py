import os
import uuid

import pytest
from fastapi.testclient import TestClient


DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("DATABASE_URL not set", allow_module_level=True)

os.environ.setdefault("STORAGE_PATH", f"/tmp/js-extractor-test-{uuid.uuid4()}")

from app.main import app


class TestApiContractHardening:
    def setup_method(self):
        self.client = TestClient(app)
        self.session_id = str(uuid.uuid4())

    def build_base_payload(self):
        js_content = "function safe(){ return 1; }"
        return {
            "metadata": {"sessionId": self.session_id},
            "files": [
                {
                    "url": "https://example.com/app.js",
                    "contentHash": "t029-hash-001",
                    "sessionId": self.session_id,
                    "capturedAt": "2026-02-09T00:00:00Z",
                    "contentType": "application/javascript",
                    "contentEncoding": "identity",
                    "contentLength": len(js_content.encode("utf-8")),
                    "content": js_content,
                    "dependencies": [],
                }
            ],
        }

    def test_save_files_rejects_invalid_file_url(self):
        payload = self.build_base_payload()
        payload["files"][0]["url"] = "ftp://example.com/app.js"
        response = self.client.post("/api/save-files", json=payload)
        assert response.status_code == 422
        assert "Invalid file url" in response.json()["detail"]

    def test_save_files_rejects_empty_content(self):
        payload = self.build_base_payload()
        payload["files"][0]["content"] = ""
        payload["files"][0]["contentLength"] = 0
        response = self.client.post("/api/save-files", json=payload)
        assert response.status_code == 422
        assert "Invalid file content" in response.json()["detail"]

    def test_save_files_rejects_invalid_dependency_resolved_url(self):
        payload = self.build_base_payload()
        payload["files"][0]["dependencies"] = [
            {"url": "./chunk.js", "resolvedUrl": "javascript:alert(1)", "type": "dynamic"}
        ]
        response = self.client.post("/api/save-files", json=payload)
        assert response.status_code == 422
        assert "Invalid dependency resolvedUrl" in response.json()["detail"]

    def test_public_dtos_hide_storage_paths(self):
        payload = self.build_base_payload()
        payload["files"][0]["sourceMapContent"] = {
            "version": 3,
            "file": "app.js",
            "sources": ["app.ts"],
            "names": [],
            "mappings": "",
        }

        ingest = self.client.post("/api/save-files", json=payload)
        assert ingest.status_code == 200
        ingest_json = ingest.json()
        file_id = ingest_json["fileIds"][0]

        ingest_sourcemap = ingest_json["files"][0]["sourceMap"]
        assert ingest_sourcemap is not None
        assert "storedPath" not in ingest_sourcemap

        file_response = self.client.get(f"/api/files/{file_id}")
        assert file_response.status_code == 200
        file_json = file_response.json()
        assert "storedPath" not in file_json
        assert "mapPath" not in file_json
        assert file_json["sourceMap"] is not None
        assert "storedPath" not in file_json["sourceMap"]

        session_response = self.client.get(f"/api/sessions/{self.session_id}/files")
        assert session_response.status_code == 200
        rows = session_response.json()
        assert len(rows) == 1
        assert rows[0]["sourceMap"] is not None
        assert "storedPath" not in rows[0]["sourceMap"]

    def test_delete_file_response_hides_deleted_paths(self):
        payload = self.build_base_payload()
        ingest = self.client.post("/api/save-files", json=payload)
        assert ingest.status_code == 200
        file_id = ingest.json()["fileIds"][0]

        response = self.client.delete(f"/api/files/{file_id}")
        assert response.status_code == 200
        body = response.json()
        assert "deletedPaths" not in body
        assert isinstance(body.get("deletedArtifactsCount"), int)

    def test_cors_allows_localhost_and_extension_origins(self):
        localhost_origin = "http://localhost:3000"
        extension_origin = "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

        localhost_response = self.client.options(
            "/health",
            headers={
                "Origin": localhost_origin,
                "Access-Control-Request-Method": "GET",
            },
        )
        assert localhost_response.status_code == 200
        assert localhost_response.headers.get("access-control-allow-origin") == localhost_origin
        assert localhost_response.headers.get("access-control-allow-credentials") == "true"

        extension_response = self.client.options(
            "/health",
            headers={
                "Origin": extension_origin,
                "Access-Control-Request-Method": "GET",
            },
        )
        assert extension_response.status_code == 200
        assert extension_response.headers.get("access-control-allow-origin") == extension_origin
        assert extension_response.headers.get("access-control-allow-credentials") == "true"

    def test_cors_rejects_unknown_origin(self):
        response = self.client.options(
            "/health",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "GET",
            },
        )

        assert response.status_code == 400
        assert response.headers.get("access-control-allow-origin") is None
