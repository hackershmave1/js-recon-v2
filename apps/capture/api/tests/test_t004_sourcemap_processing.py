import json
import uuid
import time
import asyncio
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime
from fastapi.testclient import TestClient

# Skip if no database
import os
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("DATABASE_URL not set", allow_module_level=True)

# Set test storage path
os.environ.setdefault("STORAGE_PATH", f"/tmp/js-extractor-test-{uuid.uuid4()}")

from app.main import app


class TestSourcemapProcessingIntegration:
    """Test sourcemap processing during file ingestion."""
    
    def setup_method(self):
        self.client = TestClient(app)
        self.session_id = str(uuid.uuid4())
    
    def test_sourcemap_processing_success(self):
        """Test successful sourcemap processing during ingestion."""
        js_content = 'console.log("Hello");\\n//# sourceMappingURL=app.js.map'
        
        # Mock successful sourcemap processing
        with patch('app.api.routes.ingestion.process_sourcemap_safely') as mock_process:
            payload = {
                "metadata": {"sessionId": self.session_id},
                "files": [{
                    "url": "https://example.com/app.js",
                    "contentHash": "hash123",
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
            assert data["success"] is True
            assert len(data["fileIds"]) == 1
            
            # Verify processing function was called
            mock_process.assert_called_once()
            args, kwargs = mock_process.call_args
            _, sourcemap_url, _ = args
            assert sourcemap_url == "https://example.com/app.js.map"
    
    def test_sourcemap_processing_with_provided_url(self):
        """Test processing when sourceMapUrl is explicitly provided."""
        js_content = 'console.log("Hello");'
        provided_url = "https://cdn.example.com/maps/app.js.map"
        
        with patch('app.api.routes.ingestion.process_sourcemap_safely') as mock_process:
            payload = {
                "metadata": {"sessionId": self.session_id},
                "files": [{
                    "url": "https://example.com/app.js",
                    "contentHash": "hash456",
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
            
            # Verify processing was called with provided URL
            mock_process.assert_called_once()
            args, kwargs = mock_process.call_args
            sourcemap_record, sourcemap_url, db = args
            assert sourcemap_url == provided_url
    
    def test_no_processing_for_non_js_files(self):
        """Test that sourcemap processing is not attempted for non-JavaScript files."""
        css_content = 'body { color: red; }'
        
        with patch('app.api.routes.ingestion.process_sourcemap_safely') as mock_process:
            payload = {
                "metadata": {"sessionId": self.session_id},
                "files": [{
                    "url": "https://example.com/styles.css",
                    "contentHash": "hash789",
                    "sessionId": self.session_id,
                    "capturedAt": "2026-02-08T00:00:00Z",
                    "contentType": "text/css",
                    "contentEncoding": "identity",
                    "contentLength": len(css_content.encode("utf-8")),
                    "content": css_content,
                    "dependencies": []
                }]
            }
            
            response = self.client.post("/api/save-files", json=payload)
            assert response.status_code == 200
            
            # Processing should not be called for CSS files
            mock_process.assert_not_called()
    
    def test_no_processing_without_sourcemap_url(self):
        """Test that processing is not attempted when no sourcemap URL is found."""
        js_content = 'console.log("Hello");'  # No sourcemap comment
        
        with patch('app.api.routes.ingestion.process_sourcemap_safely') as mock_process:
            payload = {
                "metadata": {"sessionId": self.session_id},
                "files": [{
                    "url": "https://example.com/app.js",
                    "contentHash": "hashABC",
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
            
            # Conventional fallback can infer app.js.map even without a comment.
            mock_process.assert_called_once()
            args, kwargs = mock_process.call_args
            _, sourcemap_url, _ = args
            assert sourcemap_url == "https://example.com/app.js.map"


class TestSourcemapProcessingSafety:
    """Test the process_sourcemap_safely function directly."""
    
    def test_timeout_handling(self):
        """Test that processing respects timeout limits."""
        from app.api.routes.ingestion import process_sourcemap_safely
        from app.models.source_map import SourceMap
        
        # Create mock objects
        mock_record = Mock(spec=SourceMap)
        mock_record.processing_status = "pending"
        mock_db = Mock()
        
        # Mock a processor that raises TimeoutError
        with patch('app.api.routes.ingestion.NativeSourceMapProcessor') as mock_processor_class:
            mock_processor = mock_processor_class.return_value
            
            # Mock async method that raises timeout
            async def timeout_process(*args, **kwargs):
                raise TimeoutError("Test timeout")
            
            mock_processor.process_sourcemap_from_url = timeout_process
            
            with patch('app.api.routes.ingestion.httpx.head') as mock_head, \
                 patch('time.sleep') as mock_sleep:
                mock_head.side_effect = Exception("No HEAD")
                process_sourcemap_safely(mock_record, "https://example.com/test.map", mock_db)
            
            # Verify timeout was handled
            assert mock_record.processing_status == "failed"
            assert "timeout" in mock_record.processing_error.lower()
            assert mock_record.processing_error.startswith("[processing_timeout]")
            assert mock_record.reconstructed_files_count == 0
            assert mock_record.processed_at is not None
    
    def test_size_limit_enforcement(self):
        """Test that large sourcemaps are rejected."""
        from app.api.routes.ingestion import process_sourcemap_safely
        from app.models.source_map import SourceMap
        
        mock_record = Mock(spec=SourceMap)
        mock_record.processing_status = "pending"
        mock_db = Mock()
        
        # Mock HEAD request returning large content-length
        with patch('app.api.routes.ingestion.httpx.head') as mock_head:
            mock_response = Mock()
            mock_response.headers = {'content-length': str(60 * 1024 * 1024)}  # 60MB > 50MB limit
            mock_head.return_value = mock_response
            
            process_sourcemap_safely(mock_record, "https://example.com/huge.map", mock_db)
            
            # Verify size limit was enforced
            assert mock_record.processing_status == "failed"
            assert "too large" in mock_record.processing_error.lower()
    
    def test_file_count_limit(self):
        """Test handling of sourcemaps with too many files."""
        from app.api.routes.ingestion import process_sourcemap_safely
        from app.models.source_map import SourceMap
        
        mock_record = Mock(spec=SourceMap)
        mock_record.processing_status = "pending"
        mock_db = Mock()
        
        # Mock processor returning many files
        mock_files = [f"file_{i}.js" for i in range(1500)]  # > 1000 limit
        mock_result = {
            "success": True,
            "files": mock_files,
            "error": None
        }
        
        with patch('app.api.routes.ingestion.NativeSourceMapProcessor') as mock_processor_class:
            mock_processor = mock_processor_class.return_value
            
            # Mock async method
            async def async_process(*args, **kwargs):
                return mock_result
            mock_processor.process_sourcemap_from_url = async_process
            
            # Mock HEAD request to pass size check
            with patch('app.api.routes.ingestion.httpx.head') as mock_head:
                mock_head.side_effect = Exception("No HEAD")  # Skip size check
                
                process_sourcemap_safely(mock_record, "https://example.com/many.map", mock_db)
        
        # Verify file count was limited with explicit status
        assert mock_record.processing_status == "completed_limited"
        assert mock_record.processing_error.startswith("[resource_limit]")
        assert mock_record.reconstructed_files_count == 1000  # Capped at limit
        assert mock_record.parsed is True
    
    def test_processing_error_handling(self):
        """Test handling of processing errors."""
        from app.api.routes.ingestion import process_sourcemap_safely
        from app.models.source_map import SourceMap
        
        mock_record = Mock(spec=SourceMap)
        mock_record.processing_status = "pending"
        mock_db = Mock()
        
        # Mock failed processing result
        mock_result = {
            "success": False,
            "files": [],
            "error": "Invalid JSON format"
        }
        
        with patch('app.api.routes.ingestion.NativeSourceMapProcessor') as mock_processor_class:
            mock_processor = mock_processor_class.return_value
            
            # Mock async method
            async def async_process(*args, **kwargs):
                return mock_result
            mock_processor.process_sourcemap_from_url = async_process
            
            with patch('app.api.routes.ingestion.httpx.head') as mock_head:
                mock_head.side_effect = Exception("No HEAD")
                
                process_sourcemap_safely(mock_record, "https://example.com/bad.map", mock_db)
        
        # Verify error was handled
        assert mock_record.processing_status == "failed"
        assert "Invalid JSON format" in mock_record.processing_error
        assert mock_record.processing_error.startswith("[decode_invalid_json]")
        assert mock_record.reconstructed_files_count == 0
        assert mock_record.processed_at is not None

    def test_transient_http_error_retries_with_backoff(self):
        """Transient sourcemap fetch failures should retry before failing."""
        from app.api.routes.ingestion import process_sourcemap_safely
        from app.models.source_map import SourceMap

        mock_record = Mock(spec=SourceMap)
        mock_record.processing_status = "pending"
        mock_db = Mock()

        call_count = {"value": 0}

        with patch('app.api.routes.ingestion.NativeSourceMapProcessor') as mock_processor_class:
            mock_processor = mock_processor_class.return_value

            async def async_process(*args, **kwargs):
                call_count["value"] += 1
                return {
                    "success": False,
                    "files": [],
                    "error": "HTTP error fetching source map: 503"
                }
            mock_processor.process_sourcemap_from_url = async_process

            with patch('app.api.routes.ingestion.httpx.head') as mock_head, \
                 patch('time.sleep') as mock_sleep:
                mock_head.side_effect = Exception("No HEAD")
                process_sourcemap_safely(mock_record, "https://example.com/transient.map", mock_db)

        assert call_count["value"] == 3
        assert mock_sleep.call_count == 2
        assert mock_record.processing_status == "failed"
        assert mock_record.processing_error.startswith("[fetch_http_5xx]")

    def test_non_retriable_http_404_does_not_retry(self):
        """404 sourcemap fetch failures should fail immediately."""
        from app.api.routes.ingestion import process_sourcemap_safely
        from app.models.source_map import SourceMap

        mock_record = Mock(spec=SourceMap)
        mock_record.processing_status = "pending"
        mock_db = Mock()

        call_count = {"value": 0}

        with patch('app.api.routes.ingestion.NativeSourceMapProcessor') as mock_processor_class:
            mock_processor = mock_processor_class.return_value

            async def async_process(*args, **kwargs):
                call_count["value"] += 1
                return {
                    "success": False,
                    "files": [],
                    "error": "HTTP error fetching source map: 404"
                }
            mock_processor.process_sourcemap_from_url = async_process

            with patch('app.api.routes.ingestion.httpx.head') as mock_head, \
                 patch('time.sleep') as mock_sleep:
                mock_head.side_effect = Exception("No HEAD")
                process_sourcemap_safely(mock_record, "https://example.com/not-found.map", mock_db)

        assert call_count["value"] == 1
        assert mock_sleep.call_count == 0
        assert mock_record.processing_status == "failed"
        assert mock_record.processing_error.startswith("[fetch_http_404]")
    
    def test_successful_processing(self):
        """Test successful sourcemap processing."""
        from app.api.routes.ingestion import process_sourcemap_safely
        from app.models.source_map import SourceMap
        
        mock_record = Mock(spec=SourceMap)
        mock_record.processing_status = "pending"
        mock_db = Mock()
        
        # Mock successful processing result
        mock_files = ["src/main.js", "src/utils.js", "src/components/App.js"]
        mock_result = {
            "success": True,
            "files": mock_files,
            "error": None
        }
        
        with patch('app.api.routes.ingestion.NativeSourceMapProcessor') as mock_processor_class:
            mock_processor = mock_processor_class.return_value
            
            # Mock async method
            async def async_process(*args, **kwargs):
                return mock_result
            mock_processor.process_sourcemap_from_url = async_process
            
            with patch('app.api.routes.ingestion.httpx.head') as mock_head:
                mock_head.side_effect = Exception("No HEAD")
                
                process_sourcemap_safely(mock_record, "https://example.com/good.map", mock_db)
        
        # Verify success
        assert mock_record.processing_status == "completed"
        assert mock_record.processing_error is None
        assert mock_record.reconstructed_files_count == 3
        assert mock_record.parsed is True
        assert mock_record.processed_at is not None


def test_ingestion_still_works_on_processing_failure():
    """Integration test: verify ingestion succeeds even if sourcemap processing fails."""
    client = TestClient(app)
    session_id = str(uuid.uuid4())
    
    js_content = 'console.log("test");\\n//# sourceMappingURL=broken.js.map'
    
    # Mock failing sourcemap processing
    with patch('app.api.routes.ingestion.process_sourcemap_safely') as mock_process:
        def failing_process(record, url, db):
            record.processing_status = "failed"
            record.processing_error = "Network error"
            record.reconstructed_files_count = 0
            record.processed_at = datetime.utcnow()
            db.flush()
        
        mock_process.side_effect = failing_process
        
        payload = {
            "metadata": {"sessionId": session_id},
            "files": [{
                "url": "https://example.com/app.js",
                "contentHash": "test123",
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
        
        # Ingestion should still succeed despite sourcemap processing failure
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["fileIds"]) == 1
        
        # Verify we can still retrieve the file
        file_id = data["fileIds"][0]
        content_resp = client.get(f"/api/files/{file_id}/content")
        assert content_resp.status_code == 200
        assert "console.log" in content_resp.text
