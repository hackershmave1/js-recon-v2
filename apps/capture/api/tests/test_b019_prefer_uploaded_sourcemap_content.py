import os
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("DATABASE_URL not set", allow_module_level=True)

os.environ.setdefault("STORAGE_PATH", f"/tmp/js-extractor-test-{uuid.uuid4()}")

from app.main import app


class TestPreferUploadedSourceMapContent:
    def setup_method(self):
        self.client = TestClient(app)
        self.session_id = str(uuid.uuid4())

    def test_prefers_uploaded_sourcemap_content_when_url_is_unreachable(self):
        js_content = "console.log('wishandwash');"
        payload = {
            "metadata": {"sessionId": self.session_id},
            "files": [
                {
                    "url": "https://wishandwash.co.il/static/js/app.min.js",
                    "contentHash": "b019-hash-content-first",
                    "sessionId": self.session_id,
                    "capturedAt": "2026-02-09T00:00:00Z",
                    "contentType": "application/javascript",
                    "contentEncoding": "identity",
                    "contentLength": len(js_content.encode("utf-8")),
                    "content": js_content,
                    "sourceMapUrl": "https://wishandwash.co.il/static/js/nonexistent-auth-only.map",
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

        with patch("app.api.routes.ingestion.process_sourcemap_content_safely") as mock_content_proc, patch(
            "app.api.routes.ingestion.process_sourcemap_safely"
        ) as mock_url_proc:
            response = self.client.post("/api/save-files", json=payload)

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["stored"] == 1
        assert body["files"][0]["sourceMap"]["mapUrl"] == payload["files"][0]["sourceMapUrl"]
        mock_content_proc.assert_called_once()
        mock_url_proc.assert_not_called()

    def test_falls_back_to_url_when_uploaded_sourcemap_content_missing(self):
        js_content = "console.log('wishandwash');"
        payload = {
            "metadata": {"sessionId": self.session_id},
            "files": [
                {
                    "url": "https://wishandwash.co.il/static/js/runtime.min.js",
                    "contentHash": "b019-hash-url-fallback",
                    "sessionId": self.session_id,
                    "capturedAt": "2026-02-09T00:00:00Z",
                    "contentType": "application/javascript",
                    "contentEncoding": "identity",
                    "contentLength": len(js_content.encode("utf-8")),
                    "content": js_content,
                    "sourceMapUrl": "https://wishandwash.co.il/static/js/runtime.min.js.map",
                    "dependencies": [],
                }
            ],
        }

        with patch("app.api.routes.ingestion.process_sourcemap_content_safely") as mock_content_proc, patch(
            "app.api.routes.ingestion.process_sourcemap_safely"
        ) as mock_url_proc:
            response = self.client.post("/api/save-files", json=payload)

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["stored"] == 1
        mock_content_proc.assert_not_called()
        mock_url_proc.assert_called_once()
