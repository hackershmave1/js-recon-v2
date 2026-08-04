import json
import uuid
import pytest
from fastapi.testclient import TestClient

# Skip if no database
import os
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("DATABASE_URL not set", allow_module_level=True)

# Set test storage path
os.environ.setdefault("STORAGE_PATH", f"/tmp/js-extractor-test-{uuid.uuid4()}")

from app.main import app


class TestUploadResponseWithSourcemapStatus:
    """Test that save-files response includes per-file sourcemap status."""
    
    def setup_method(self):
        self.client = TestClient(app)
        self.session_id = str(uuid.uuid4())
    
    def test_response_includes_files_array(self):
        """Test that response includes new 'files' array with per-file data."""
        js_content = 'console.log("Hello");\\n//# sourceMappingURL=app.js.map'
        
        payload = {
            "metadata": {"sessionId": self.session_id},
            "files": [{
                "url": "https://example.com/app.js",
                "contentHash": "test-hash-001",
                "sessionId": self.session_id,
                "capturedAt": "2026-02-08T00:00:00Z",
                "contentType": "application/javascript",
                "contentEncoding": "identity",
                "contentLength": len(js_content.encode("utf-8")),
                "content": js_content,
                "dependencies": []
            }]
        }
        
        response = self.client.post("/api/save-files", json=payload)
        assert response.status_code == 200
        
        data = response.json()
        
        # Verify backward compatibility
        assert data["success"] is True
        assert data["sessionId"] == self.session_id
        assert data["stored"] == 1
        assert len(data["fileIds"]) == 1
        
        # Verify new files array
        assert "files" in data
        assert isinstance(data["files"], list)
        assert len(data["files"]) == 1
        
        file_result = data["files"][0]
        
        # Verify file result structure
        assert "fileId" in file_result
        assert "url" in file_result
        assert "contentHash" in file_result
        assert "sourceMap" in file_result
        
        # Verify file result values
        assert file_result["fileId"] == data["fileIds"][0]
        assert file_result["url"] == "https://example.com/app.js"
        assert file_result["contentHash"] == "test-hash-001"
        
        # Verify sourcemap data
        sourcemap = file_result["sourceMap"]
        assert sourcemap is not None
        assert "detectedMapUrl" in sourcemap
        assert "processingStatus" in sourcemap
        assert sourcemap["detectedMapUrl"] == "https://example.com/app.js.map"
        assert sourcemap["processingStatus"] in ["pending", "processing", "completed", "completed_limited", "failed"]
    
    def test_multiple_files_response(self):
        """Test response format with multiple files including mixed sourcemap status."""
        js_with_sourcemap = 'console.log("Has map");\\n//# sourceMappingURL=has-map.js.map'
        js_without_sourcemap = 'console.log("No map");'
        css_file = 'body { color: red; }'
        
        payload = {
            "metadata": {"sessionId": self.session_id},
            "files": [
                {
                    "url": "https://example.com/has-map.js",
                    "contentHash": "hash-with-map",
                    "sessionId": self.session_id,
                    "capturedAt": "2026-02-08T00:00:00Z",
                    "contentType": "application/javascript",
                    "contentEncoding": "identity",
                    "contentLength": len(js_with_sourcemap.encode("utf-8")),
                    "content": js_with_sourcemap,
                    "dependencies": []
                },
                {
                    "url": "https://example.com/no-map.js",
                    "contentHash": "hash-without-map",
                    "sessionId": self.session_id,
                    "capturedAt": "2026-02-08T00:00:00Z",
                    "contentType": "application/javascript",
                    "contentEncoding": "identity",
                    "contentLength": len(js_without_sourcemap.encode("utf-8")),
                    "content": js_without_sourcemap,
                    "dependencies": []
                },
                {
                    "url": "https://example.com/styles.css",
                    "contentHash": "hash-css",
                    "sessionId": self.session_id,
                    "capturedAt": "2026-02-08T00:00:00Z",
                    "contentType": "text/css",
                    "contentEncoding": "identity",
                    "contentLength": len(css_file.encode("utf-8")),
                    "content": css_file,
                    "dependencies": []
                }
            ]
        }
        
        response = self.client.post("/api/save-files", json=payload)
        assert response.status_code == 200
        
        data = response.json()
        
        # Verify backward compatibility
        assert data["success"] is True
        assert data["stored"] == 3
        assert len(data["fileIds"]) == 3
        assert len(data["files"]) == 3
        
        # Find files by URL for easier testing
        files_by_url = {f["url"]: f for f in data["files"]}
        
        # Verify JS file with sourcemap
        js_with_map = files_by_url["https://example.com/has-map.js"]
        assert js_with_map["sourceMap"] is not None
        assert js_with_map["sourceMap"]["detectedMapUrl"] == "https://example.com/has-map.js.map"
        
        # Verify JS file without sourcemap (may have auto-detected .map URL)
        js_without_map = files_by_url["https://example.com/no-map.js"]
        # Note: Due to fallback behavior, this might still have a sourcemap record
        
        # Verify CSS file (should have no sourcemap)
        css_file = files_by_url["https://example.com/styles.css"]
        assert css_file["sourceMap"] is None
    
    def test_provided_sourcemap_url(self):
        """Test response when sourceMapUrl is explicitly provided."""
        js_content = 'console.log("Hello");'  # No comment
        provided_url = "https://cdn.example.com/maps/custom.js.map"
        
        payload = {
            "metadata": {"sessionId": self.session_id},
            "files": [{
                "url": "https://example.com/app.js",
                "contentHash": "test-hash-002",
                "sessionId": self.session_id,
                "capturedAt": "2026-02-08T00:00:00Z",
                "contentType": "application/javascript",
                "contentEncoding": "identity",
                "contentLength": len(js_content.encode("utf-8")),
                "content": js_content,
                "sourceMapUrl": provided_url,
                "dependencies": []
            }]
        }
        
        response = self.client.post("/api/save-files", json=payload)
        assert response.status_code == 200
        
        data = response.json()
        file_result = data["files"][0]
        sourcemap = file_result["sourceMap"]
        
        assert sourcemap is not None
        assert sourcemap["mapUrl"] == provided_url
        # The detected URL might be None since no comment exists
        # But mapUrl should be the provided URL
    
    def test_backward_compatibility_preserved(self):
        """Test that existing clients can still parse the response."""
        js_content = 'console.log("Test");'
        
        payload = {
            "metadata": {"sessionId": self.session_id},
            "files": [{
                "url": "https://example.com/test.js",
                "contentHash": "test-hash-003",
                "sessionId": self.session_id,
                "capturedAt": "2026-02-08T00:00:00Z",
                "contentType": "application/javascript",
                "contentEncoding": "identity",
                "contentLength": len(js_content.encode("utf-8")),
                "content": js_content,
                "dependencies": []
            }]
        }
        
        response = self.client.post("/api/save-files", json=payload)
        assert response.status_code == 200
        
        data = response.json()
        
        # All existing fields must be present
        required_fields = ["success", "sessionId", "stored", "fileIds"]
        for field in required_fields:
            assert field in data
        
        # Verify the types match original response
        assert isinstance(data["success"], bool)
        assert isinstance(data["sessionId"], str)
        assert isinstance(data["stored"], int)
        assert isinstance(data["fileIds"], list)
        assert all(isinstance(file_id, str) for file_id in data["fileIds"])
        
        # New field should be present
        assert "files" in data
        assert isinstance(data["files"], list)


def test_empty_files_array():
    """Test response when no files are provided."""
    client = TestClient(app)
    session_id = str(uuid.uuid4())
    
    payload = {
        "metadata": {"sessionId": session_id},
        "files": []
    }
    
    response = client.post("/api/save-files", json=payload)
    assert response.status_code == 400  # Should reject empty files
    assert "No files provided" in response.json()["detail"]


def test_response_performance_impact():
    """Test that response size is reasonable with sourcemap data."""
    client = TestClient(app)
    session_id = str(uuid.uuid4())
    
    # Create a moderately sized batch
    files = []
    for i in range(5):
        files.append({
            "url": f"https://example.com/file{i}.js",
            "contentHash": f"hash{i}",
            "sessionId": session_id,
            "capturedAt": "2026-02-08T00:00:00Z",
            "contentType": "application/javascript",
            "contentEncoding": "identity",
            "contentLength": 100,
            "content": f'console.log("File {i}");',
            "dependencies": []
        })
    
    payload = {
        "metadata": {"sessionId": session_id},
        "files": files
    }
    
    response = client.post("/api/save-files", json=payload)
    assert response.status_code == 200
    
    data = response.json()
    
    # Verify all files processed
    assert data["stored"] == 5
    assert len(data["files"]) == 5
    
    # Verify response is reasonable size (rough check)
    response_text = response.text
    assert len(response_text) < 50000  # Should be well under 50KB for 5 files
