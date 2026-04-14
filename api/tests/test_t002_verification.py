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


def test_file_endpoint_includes_sourcemap_fields():
    """Test that /api/files/{file_id} includes sourcemap state fields."""
    client = TestClient(app)
    session_id = str(uuid.uuid4())
    
    # Upload a JavaScript file with sourcemap
    js_content = 'console.log("Hello");\\n//# sourceMappingURL=app.js.map'
    payload = {
        "metadata": {"sessionId": session_id},
        "files": [{
            "url": "https://example.com/app.js",
            "contentHash": "test-hash-123",
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
    file_id = data["fileIds"][0]
    
    # Get file metadata
    file_response = client.get(f"/api/files/{file_id}")
    assert file_response.status_code == 200
    
    file_data = file_response.json()
    
    # Verify sourcemap fields are present
    assert "sourceMap" in file_data
    sourcemap = file_data["sourceMap"]
    
    # Check T-001 fields are included
    assert "detectedMapUrl" in sourcemap
    assert "processingStatus" in sourcemap
    assert "processingError" in sourcemap
    assert "reconstructedFilesCount" in sourcemap
    assert "processedAt" in sourcemap
    
    # Verify values match what we expect
    assert sourcemap["detectedMapUrl"] == "https://example.com/app.js.map"
    assert sourcemap["processingStatus"] in ["pending", "processing", "completed", "completed_limited", "failed"]
    assert isinstance(sourcemap["reconstructedFilesCount"], int)


def test_session_files_endpoint_includes_sourcemap_fields():
    """Test that /api/sessions/{session_id}/files includes sourcemap state fields."""
    client = TestClient(app)
    session_id = str(uuid.uuid4())
    
    # Upload a JavaScript file with sourcemap
    js_content = 'console.log("Hello");\\n//# sourceMappingURL=test.js.map'
    payload = {
        "metadata": {"sessionId": session_id},
        "files": [{
            "url": "https://example.com/test.js",
            "contentHash": "test-hash-456",
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
    
    # Get session files
    files_response = client.get(f"/api/sessions/{session_id}/files")
    assert files_response.status_code == 200
    
    files_data = files_response.json()
    assert len(files_data) >= 1
    
    file_data = files_data[0]
    
    # Verify sourcemap fields are present in session file listing
    assert "sourceMap" in file_data
    sourcemap = file_data["sourceMap"]
    
    # Check T-001 fields are included
    assert "detectedMapUrl" in sourcemap
    assert "processingStatus" in sourcemap
    assert "processingError" in sourcemap
    assert "reconstructedFilesCount" in sourcemap
    assert "processedAt" in sourcemap
    
    # Verify values
    assert sourcemap["detectedMapUrl"] == "https://example.com/test.js.map"
    assert sourcemap["processingStatus"] in ["pending", "processing", "completed", "completed_limited", "failed"]


def test_file_without_sourcemap_has_null_fields():
    """Test that files without sourcemaps have proper null handling."""
    client = TestClient(app)
    session_id = str(uuid.uuid4())
    
    # Upload a JavaScript file without sourcemap
    js_content = 'console.log("No sourcemap");'
    payload = {
        "metadata": {"sessionId": session_id},
        "files": [{
            "url": "https://example.com/no-map.js",
            "contentHash": "test-hash-789",
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
    file_id = data["fileIds"][0]
    
    # Get file metadata
    file_response = client.get(f"/api/files/{file_id}")
    assert file_response.status_code == 200
    
    file_data = file_response.json()
    
    # File might not have a sourceMap section if no sourcemap was detected/created
    # This depends on the implementation - let's check what actually happens
    print(f"File data keys: {list(file_data.keys())}")
    if "sourceMap" in file_data:
        sourcemap = file_data["sourceMap"]
        # If sourceMap exists, detectedMapUrl should be null/None
        assert sourcemap.get("detectedMapUrl") is None
    else:
        # No sourceMap section is also valid for files without sourcemaps
        pass
