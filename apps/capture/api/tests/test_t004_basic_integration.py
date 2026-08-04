import os
import uuid
import pytest
from fastapi.testclient import TestClient

# Skip if no database
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("DATABASE_URL not set", allow_module_level=True)

# Set test storage path
os.environ.setdefault("STORAGE_PATH", f"/tmp/js-extractor-test-{uuid.uuid4()}")

from app.main import app


def test_ingestion_with_sourcemap_detection():
    """Basic test that ingestion works with sourcemap detection."""
    client = TestClient(app)
    session_id = str(uuid.uuid4())
    
    js_content = 'console.log("Hello");\\n//# sourceMappingURL=app.js.map'
    
    payload = {
        "metadata": {"sessionId": session_id},
        "files": [{
            "url": "https://example.com/app.js",
            "contentHash": "hash123",
            "sessionId": session_id,
            "capturedAt": "2026-02-08T00:00:00Z",
            "contentType": "application/javascript",
            "contentEncoding": "identity",
            "contentLength": len(js_content.encode("utf-8")),
            "content": js_content,
            "dependencies": []
        }]
    }
    
    response = client.post("/api/save-files", json=payload)
    assert response.status_code == 200
    
    data = response.json()
    assert data["success"] is True
    assert len(data["fileIds"]) == 1
    
    # Verify we can still retrieve the file
    file_id = data["fileIds"][0]
    content_resp = client.get(f"/api/files/{file_id}/content")
    assert content_resp.status_code == 200
    assert "console.log" in content_resp.text


def test_ingestion_without_sourcemap():
    """Test that ingestion still works for JS files without sourcemaps."""
    client = TestClient(app)
    session_id = str(uuid.uuid4())
    
    js_content = 'console.log("Hello");'  # No sourcemap comment
    
    payload = {
        "metadata": {"sessionId": session_id},
        "files": [{
            "url": "https://example.com/app.js",
            "contentHash": "hashABC",
            "sessionId": session_id,
            "capturedAt": "2026-02-08T00:00:00Z",
            "contentType": "application/javascript",
            "contentEncoding": "identity",
            "contentLength": len(js_content.encode("utf-8")),
            "content": js_content,
            "dependencies": []
        }]
    }
    
    response = client.post("/api/save-files", json=payload)
    assert response.status_code == 200
    
    data = response.json()
    assert data["success"] is True
    assert len(data["fileIds"]) == 1


def test_ingestion_non_js_file():
    """Test that non-JS files are not affected."""
    client = TestClient(app)
    session_id = str(uuid.uuid4())
    
    css_content = 'body { color: red; }'
    
    payload = {
        "metadata": {"sessionId": session_id},
        "files": [{
            "url": "https://example.com/styles.css",
            "contentHash": "hash789",
            "sessionId": session_id,
            "capturedAt": "2026-02-08T00:00:00Z",
            "contentType": "text/css",
            "contentEncoding": "identity",
            "contentLength": len(css_content.encode("utf-8")),
            "content": css_content,
            "dependencies": []
        }]
    }
    
    response = client.post("/api/save-files", json=payload)
    assert response.status_code == 200
    
    data = response.json()
    assert data["success"] is True
    assert len(data["fileIds"]) == 1