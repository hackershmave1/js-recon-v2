"""Integration tests for Vespasian API additions.

Tests the hasOpenApiSpec field in GET /api/sessions and the
GET /api/sessions/{id}/openapi download endpoint.

Requires DATABASE_URL environment variable (skipped otherwise).
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("DATABASE_URL not set", allow_module_level=True)

STORAGE_PATH = f"/tmp/js-extractor-vespasian-api-test-{uuid.uuid4()}"
os.environ["STORAGE_PATH"] = STORAGE_PATH

from app.main import app


class TestHasOpenApiSpec:
    """GET /api/sessions returns hasOpenApiSpec reflecting file presence."""

    def setup_method(self):
        self.client = TestClient(app)
        self.session_id = str(uuid.uuid4())
        self._create_session()

    def _create_session(self):
        js_content = "var x = 1;"
        payload = {
            "metadata": {"sessionId": self.session_id},
            "files": [{
                "url": "https://example.com/app.js",
                "contentHash": f"t031-{self.session_id[:8]}",
                "sessionId": self.session_id,
                "capturedAt": "2026-04-15T00:00:00Z",
                "contentType": "application/javascript",
                "contentEncoding": "identity",
                "contentLength": len(js_content.encode()),
                "content": js_content,
                "dependencies": [],
            }],
        }
        resp = self.client.post("/api/save-files", json=payload)
        assert resp.status_code == 200

    def _spec_path(self) -> Path:
        return Path(STORAGE_PATH) / "sessions" / self.session_id / "openapi.yaml"

    def test_has_openapi_spec_false_when_no_file(self):
        sessions = self.client.get("/api/sessions").json()
        session = next((s for s in sessions if s["id"] == self.session_id), None)
        assert session is not None
        assert session["hasOpenApiSpec"] is False

    def test_has_openapi_spec_true_when_file_exists(self):
        path = self._spec_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("openapi: '3.0.0'\ninfo:\n  title: Test\n")

        sessions = self.client.get("/api/sessions").json()
        session = next((s for s in sessions if s["id"] == self.session_id), None)
        assert session is not None
        assert session["hasOpenApiSpec"] is True

        path.unlink()  # cleanup


class TestOpenApiDownloadEndpoint:
    """GET /api/sessions/{id}/openapi streams openapi.yaml or returns 404."""

    def setup_method(self):
        self.client = TestClient(app)
        self.session_id = str(uuid.uuid4())

    def _spec_path(self) -> Path:
        return Path(STORAGE_PATH) / "sessions" / self.session_id / "openapi.yaml"

    def test_returns_404_when_no_spec(self):
        resp = self.client.get(f"/api/sessions/{self.session_id}/openapi")
        assert resp.status_code == 404
        assert "No OpenAPI spec" in resp.json()["detail"]

    def test_returns_yaml_file_when_spec_exists(self):
        spec_content = "openapi: '3.0.0'\ninfo:\n  title: My API\n  version: '1.0'\npaths: {}\n"
        path = self._spec_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(spec_content)

        resp = self.client.get(f"/api/sessions/{self.session_id}/openapi")
        assert resp.status_code == 200
        assert resp.headers["content-type"] in {
            "application/yaml", "application/yaml; charset=utf-8",
            "text/yaml", "text/yaml; charset=utf-8",
        }
        assert "attachment" in resp.headers.get("content-disposition", "")
        assert spec_content in resp.text

        path.unlink()  # cleanup

    def test_filename_includes_session_id_prefix(self):
        path = self._spec_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("openapi: '3.0.0'\n")

        resp = self.client.get(f"/api/sessions/{self.session_id}/openapi")
        disposition = resp.headers.get("content-disposition", "")
        assert self.session_id[:8] in disposition

        path.unlink()

    def test_invalid_session_id_returns_404(self):
        resp = self.client.get("/api/sessions/not-a-uuid/openapi")
        assert resp.status_code == 404
