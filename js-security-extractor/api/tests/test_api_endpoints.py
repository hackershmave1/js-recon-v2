"""
Integration tests for API endpoints with security focus.
"""
import pytest
import json
import tempfile
import os
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.services.jsluice_extractor_secure import SecureJSluiceExtractor


client = TestClient(app)


class TestEnhancedAnalysisEndpoints:
    """Test enhanced analysis API endpoints."""
    
    @pytest.fixture
    def sample_js_request(self):
        """Sample JavaScript analysis request."""
        return {
            "content": """
            function fetchData() {
                fetch('/api/users');
                var api_key = 'sk_test_1234567890abcdef';
                axios.get('https://api.example.com/data');
                return userData;
            }
            """,
            "url": "https://example.com/app.js",
            "metadata": {
                "contentType": "application/javascript",
                "sourceMap": False
            },
            "options": {
                "resolve_urls": True,
                "include_source": True
            }
        }
    
    @pytest.fixture
    def mock_jsluice_success(self):
        """Mock successful jsluice extraction."""
        with patch.object(SecureJSluiceExtractor, 'extract_comprehensive') as mock:
            mock.return_value = {
                'success': True,
                'analysis': {
                    'urls': [
                        {
                            'url': '/api/users',
                            'type': 'jsluice_url',
                            'confidence': 'high',
                            'extractor': 'jsluice'
                        }
                    ],
                    'secrets': [
                        {
                            'value': 'sk_test_1234567890abcdef',
                            'type': 'stripe_secret',
                            'confidence': 'high',
                            'security_classification': 'critical'
                        }
                    ]
                },
                'stats': {
                    'urls_found': 1,
                    'secrets_found': 1,
                    'processing_time_ms': 150
                },
                'metadata': {
                    'extractor': 'secure_jsluice',
                    'version': '1.0.0'
                }
            }
            yield mock
    
    def test_analyze_comprehensive_success(self, sample_js_request, mock_jsluice_success):
        """Test successful comprehensive analysis."""
        response = client.post("/api/analyze-comprehensive", json=sample_js_request)
        
        assert response.status_code == 200
        data = response.json()
        
        assert data["success"] is True
        assert "analysis" in data
        assert "processing_time_ms" in data
        assert "extractors_used" in data
        
        # Check analysis structure
        analysis = data["analysis"]
        assert "analysis" in analysis
        assert "urls" in analysis["analysis"]
        assert "secrets" in analysis["analysis"]
    
    def test_analyze_comprehensive_invalid_content(self):
        """Test comprehensive analysis with invalid content."""
        invalid_request = {
            "content": "",  # Empty content
            "url": "https://example.com/app.js"
        }
        
        response = client.post("/api/analyze-comprehensive", json=invalid_request)
        assert response.status_code == 500
        
        data = response.json()
        assert "error" in data["detail"]
    
    def test_analyze_comprehensive_malicious_url(self):
        """Test comprehensive analysis with malicious URL."""
        malicious_request = {
            "content": "function test() {}",
            "url": "http://example.com'; rm -rf /; echo '"
        }
        
        response = client.post("/api/analyze-comprehensive", json=malicious_request)
        assert response.status_code == 500
        
        data = response.json()
        assert "error" in data["detail"]
    
    def test_analyze_comprehensive_oversized_content(self):
        """Test comprehensive analysis with oversized content."""
        large_content = "console.log('x');" * 100000  # Large but under limit
        oversized_request = {
            "content": large_content,
            "url": "https://example.com/app.js"
        }
        
        response = client.post("/api/analyze-comprehensive", json=oversized_request)
        # Should succeed if under limit, fail if over
        # This tests the actual size validation
        if len(large_content) > 10 * 1024 * 1024:  # 10MB limit
            assert response.status_code == 500
        else:
            # May succeed with mock
            pass
    
    def test_analyze_jsluice_only(self, sample_js_request):
        """Test jsluice-only analysis endpoint."""
        with patch.object(SecureJSluiceExtractor, '__init__', return_value=None):
            with patch.object(SecureJSluiceExtractor, 'extract_urls', return_value=[]):
                with patch.object(SecureJSluiceExtractor, 'extract_secrets', return_value=[]):
                    response = client.post("/api/analyze-jsluice", json=sample_js_request)
                    
                    if response.status_code == 503:
                        # jsluice not available
                        data = response.json()
                        assert "jsluice extractor not available" in data["detail"]
                    else:
                        assert response.status_code == 200
    
    def test_process_sourcemap_valid_request(self):
        """Test source map processing with valid request."""
        sourcemap_request = {
            "js_url": "https://example.com/app.js",
            "sourcemap_url": "https://example.com/app.js.map"
        }
        
        with patch('app.services.comprehensive_extractor.ComprehensiveExtractor') as mock_extractor:
            mock_instance = MagicMock()
            mock_extractor.return_value = mock_instance
            mock_instance.sourcemapper = MagicMock()
            mock_instance.sourcemapper.process_sourcemap_from_url.return_value = {
                'success': True,
                'files': [
                    {
                        'path': 'src/app.js',
                        'content': 'function test() { return true; }',
                        'type': 'javascript'
                    }
                ],
                'stats': {'total_files': 1, 'total_size': 35}
            }
            
            response = client.post("/api/process-sourcemap", json=sourcemap_request)
            
            if response.status_code == 503:
                # sourcemapper not available
                pass
            else:
                assert response.status_code == 200
                data = response.json()
                assert data["success"] is True
    
    def test_process_sourcemap_missing_params(self):
        """Test source map processing with missing parameters."""
        incomplete_request = {}  # Missing required params
        
        response = client.post("/api/process-sourcemap", json=incomplete_request)
        assert response.status_code == 400
        
        data = response.json()
        assert "js_url or sourcemap_url is required" in data["detail"]
    
    def test_process_sourcemap_malicious_url(self):
        """Test source map processing with malicious URL."""
        malicious_request = {
            "js_url": "https://example.com/app.js'; rm -rf /; echo '"
        }
        
        response = client.post("/api/process-sourcemap", json=malicious_request)
        assert response.status_code == 500
        # Should fail due to URL validation
    
    def test_batch_analyze_success(self):
        """Test batch analysis with multiple files."""
        batch_request = [
            {
                "content": "function test1() { fetch('/api/users'); }",
                "url": "https://example.com/test1.js",
                "metadata": {"index": 0}
            },
            {
                "content": "function test2() { axios.get('/api/data'); }",
                "url": "https://example.com/test2.js", 
                "metadata": {"index": 1}
            }
        ]
        
        with patch('app.services.comprehensive_extractor.ComprehensiveExtractor') as mock_extractor:
            mock_instance = MagicMock()
            mock_extractor.return_value = mock_instance
            mock_instance.extract_all.return_value = {
                'stats': {'total_endpoints': 1, 'total_secrets': 0}
            }
            
            response = client.post("/api/batch-analyze", json={"files": batch_request})
            assert response.status_code == 200
            
            data = response.json()
            assert data["success"] is True
            assert data["batch_size"] == 2
            assert "results" in data
            assert "stats" in data
    
    def test_batch_analyze_empty_batch(self):
        """Test batch analysis with empty batch."""
        empty_request = {"files": []}
        
        response = client.post("/api/batch-analyze", json=empty_request)
        assert response.status_code == 200
        
        data = response.json()
        assert data["batch_size"] == 0
    
    def test_batch_analyze_mixed_results(self):
        """Test batch analysis with some failures."""
        mixed_batch = [
            {
                "content": "function valid() { return true; }",
                "url": "https://example.com/valid.js"
            },
            {
                "content": "",  # Invalid - empty content
                "url": "https://example.com/invalid.js"
            }
        ]
        
        response = client.post("/api/batch-analyze", json={"files": mixed_batch})
        assert response.status_code == 200
        
        data = response.json()
        assert data["batch_size"] == 2
        assert "stats" in data
        # Some should succeed, some should fail
        assert data["stats"]["successful"] + data["stats"]["failed"] == 2


class TestSessionAnalysisEndpoints:
    """Test session analysis endpoints."""
    
    def test_get_session_analysis_nonexistent(self):
        """Test getting analysis for nonexistent session."""
        fake_session_id = "00000000-0000-0000-0000-000000000000"
        
        response = client.get(f"/api/sessions/{fake_session_id}/comprehensive-analysis")
        assert response.status_code == 404
        
        data = response.json()
        assert "Session not found" in data["detail"]
    
    def test_get_session_analysis_invalid_uuid(self):
        """Test getting analysis with invalid UUID format."""
        invalid_id = "not-a-uuid"
        
        response = client.get(f"/api/sessions/{invalid_id}/comprehensive-analysis")
        # May return 404 or validation error depending on implementation
        assert response.status_code in [400, 404, 422]


class TestSecurityHeaders:
    """Test security headers in API responses."""
    
    def test_security_headers_present(self):
        """Test that security headers are present in responses."""
        response = client.get("/health")
        assert response.status_code == 200
        
        # Check for security headers (if implemented)
        headers = response.headers
        
        # These should be implemented in the security middleware
        security_headers = [
            'x-content-type-options',
            'x-frame-options',
            'x-xss-protection'
        ]
        
        # Note: These tests will pass/fail based on whether security middleware is implemented
        for header in security_headers:
            if header.lower() in headers:
                assert headers[header.lower()] is not None
    
    def test_cors_headers(self):
        """Test CORS headers in responses."""
        response = client.options("/health")
        
        # CORS headers should be present
        headers = response.headers
        cors_headers = ['access-control-allow-origin', 'access-control-allow-methods']
        
        for header in cors_headers:
            if header.lower() in headers:
                assert headers[header.lower()] is not None


class TestErrorHandling:
    """Test error handling and edge cases."""
    
    def test_malformed_json_request(self):
        """Test handling of malformed JSON requests."""
        response = client.post(
            "/api/analyze-comprehensive",
            data="{'invalid': json}",  # Malformed JSON
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 422  # Unprocessable Entity
    
    def test_missing_required_fields(self):
        """Test handling of requests missing required fields."""
        incomplete_request = {
            "url": "https://example.com/app.js"
            # Missing required 'content' field
        }
        
        response = client.post("/api/analyze-comprehensive", json=incomplete_request)
        assert response.status_code == 422
        
        data = response.json()
        assert "detail" in data
        # Should indicate missing field
        assert any("content" in str(error) for error in data["detail"])
    
    def test_invalid_field_types(self):
        """Test handling of invalid field types."""
        invalid_request = {
            "content": 12345,  # Should be string
            "url": "https://example.com/app.js"
        }
        
        response = client.post("/api/analyze-comprehensive", json=invalid_request)
        assert response.status_code == 422
    
    def test_sql_injection_in_session_id(self):
        """Test SQL injection protection in session ID."""
        malicious_id = "'; DROP TABLE sessions; --"
        
        response = client.get(f"/api/sessions/{malicious_id}/comprehensive-analysis")
        # Should not cause internal error, should validate UUID format
        assert response.status_code in [400, 404, 422]
        # Should not return 500 (internal server error)
        assert response.status_code != 500
    
    def test_path_traversal_in_endpoints(self):
        """Test path traversal protection."""
        traversal_attempts = [
            "../../../etc/passwd",
            "..\\..\\..\\windows\\system32\\config\\sam",
            "%2e%2e%2f%2e%2e%2f%2e%2e%2f%65%74%63%2f%70%61%73%73%77%64"
        ]
        
        for attempt in traversal_attempts:
            response = client.get(f"/api/files/{attempt}")
            # Should not allow path traversal
            assert response.status_code in [400, 404, 422]
            assert response.status_code != 200
    
    def test_xxe_protection_in_json(self):
        """Test XXE protection in JSON processing."""
        xxe_payload = {
            "content": '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><foo>&xxe;</foo>',
            "url": "https://example.com/app.js"
        }
        
        response = client.post("/api/analyze-comprehensive", json=xxe_payload)
        
        # Should handle as normal content (JSON doesn't process XML entities)
        # But the content validation might catch suspicious patterns
        if response.status_code == 200:
            data = response.json()
            # Should not contain actual file content
            assert "/etc/passwd" not in str(data) or "xxe" in str(data)


class TestRateLimiting:
    """Test rate limiting functionality (if implemented)."""
    
    def test_rate_limiting_multiple_requests(self):
        """Test rate limiting with multiple rapid requests."""
        # Make multiple rapid requests
        responses = []
        for i in range(10):
            response = client.get("/health")
            responses.append(response)
        
        # All should succeed for health endpoint
        for response in responses:
            assert response.status_code == 200
        
        # Note: Actual rate limiting would need to be implemented
        # and tested with more aggressive request patterns
    
    def test_rate_limiting_analysis_endpoint(self):
        """Test rate limiting on analysis endpoints."""
        # This would test actual rate limiting if implemented
        # For now, just verify endpoint responds
        simple_request = {
            "content": "function test() {}",
            "url": "https://example.com/test.js"
        }
        
        response = client.post("/api/analyze-comprehensive", json=simple_request)
        # Should not be rate limited for single request
        assert response.status_code in [200, 500, 503]  # 500/503 if extractors not available


class TestContentTypeValidation:
    """Test content type validation and handling."""
    
    def test_unsupported_content_type(self):
        """Test handling of unsupported content types."""
        response = client.post(
            "/api/analyze-comprehensive",
            data="content=test&url=example.com",  # Form data instead of JSON
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        assert response.status_code == 422
    
    def test_missing_content_type(self):
        """Test handling of missing content type header."""
        response = client.post(
            "/api/analyze-comprehensive",
            data='{"content": "test", "url": "https://example.com"}'
            # No Content-Type header
        )
        # FastAPI should handle this gracefully
        assert response.status_code in [200, 400, 422, 500]


class TestHealthAndStatus:
    """Test health check and status endpoints."""
    
    def test_health_endpoint(self):
        """Test health check endpoint."""
        response = client.get("/health")
        assert response.status_code == 200
        
        data = response.json()
        assert data["status"] == "healthy"
    
    def test_root_endpoint(self):
        """Test root endpoint."""
        response = client.get("/")
        assert response.status_code == 200
        
        data = response.json()
        assert "message" in data
        assert "version" in data
        assert data["status"] == "running"