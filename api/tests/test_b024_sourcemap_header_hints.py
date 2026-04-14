"""
Tests for B-024 - SourceMap Header Hint Support

Tests sourcemap detection from HTTP response headers (SourceMap, X-SourceMap)
alongside existing comment-based detection.
"""

import pytest
from app.api.routes.ingestion import detect_sourcemap_url


class TestSourceMapHeaderHints:
    """Test sourcemap detection from HTTP headers."""
    
    def test_sourcemap_header_detection(self):
        """Test detection from standard SourceMap header."""
        js_content = "console.log('test');"
        js_url = "https://example.com/app.js"
        headers = {"SourceMap": "app.js.map"}
        
        url, method = detect_sourcemap_url(js_content, js_url, headers)
        
        assert url == "https://example.com/app.js.map"
        assert method == "header"
    
    def test_x_sourcemap_header_detection(self):
        """Test detection from X-SourceMap header."""
        js_content = "console.log('test');"
        js_url = "https://example.com/app.js"
        headers = {"X-SourceMap": "maps/app.js.map"}
        
        url, method = detect_sourcemap_url(js_content, js_url, headers)
        
        assert url == "https://example.com/maps/app.js.map"
        assert method == "header"
    
    def test_case_insensitive_header_detection(self):
        """Test that header detection is case-insensitive."""
        js_content = "console.log('test');"
        js_url = "https://example.com/app.js"
        
        test_cases = [
            {"sourcemap": "app.js.map"},
            {"SOURCEMAP": "app.js.map"},
            {"SourceMap": "app.js.map"},
            {"x-sourcemap": "app.js.map"},
            {"X-SOURCEMAP": "app.js.map"},
            {"X-SourceMap": "app.js.map"},
        ]
        
        for headers in test_cases:
            url, method = detect_sourcemap_url(js_content, js_url, headers)
            assert url == "https://example.com/app.js.map", f"Failed for headers: {headers}"
            assert method == "header"
    
    def test_absolute_url_in_header(self):
        """Test that absolute URLs in headers are preserved."""
        js_content = "console.log('test');"
        js_url = "https://example.com/app.js"
        headers = {"SourceMap": "https://cdn.example.com/maps/app.js.map"}
        
        url, method = detect_sourcemap_url(js_content, js_url, headers)
        
        assert url == "https://cdn.example.com/maps/app.js.map"
        assert method == "header"
    
    def test_data_url_in_header(self):
        """Test that data URLs in headers are preserved."""
        js_content = "console.log('test');"
        js_url = "https://example.com/app.js"
        data_url = "data:application/json;base64,eyJ2ZXJzaW9uIjozfQ=="
        headers = {"SourceMap": data_url}
        
        url, method = detect_sourcemap_url(js_content, js_url, headers)
        
        assert url == data_url
        assert method == "header"
    
    def test_header_priority_over_content(self):
        """Test that header hints take priority over content comments."""
        js_content = "console.log('test');//# sourceMappingURL=content.js.map"
        js_url = "https://example.com/app.js"
        headers = {"SourceMap": "header.js.map"}
        
        url, method = detect_sourcemap_url(js_content, js_url, headers)
        
        assert url == "https://example.com/header.js.map"  # Header takes priority
        assert method == "header"
    
    def test_fallback_to_content_when_no_header(self):
        """Test fallback to content detection when no header present."""
        js_content = "console.log('test');//# sourceMappingURL=content.js.map"
        js_url = "https://example.com/app.js"
        headers = {"Content-Type": "application/javascript"}  # No sourcemap headers
        
        url, method = detect_sourcemap_url(js_content, js_url, headers)
        
        assert url == "https://example.com/content.js.map"
        assert method == "content"
    
    def test_fallback_to_content_when_no_headers(self):
        """Test fallback to content detection when headers is None."""
        js_content = "console.log('test');//# sourceMappingURL=content.js.map"
        js_url = "https://example.com/app.js"
        
        url, method = detect_sourcemap_url(js_content, js_url, None)
        
        assert url == "https://example.com/content.js.map"
        assert method == "content"
    
    def test_empty_header_value_ignored(self):
        """Test that empty header values are ignored."""
        js_content = "console.log('test');//# sourceMappingURL=content.js.map"
        js_url = "https://example.com/app.js"
        headers = {"SourceMap": ""}  # Empty value
        
        url, method = detect_sourcemap_url(js_content, js_url, headers)
        
        assert url == "https://example.com/content.js.map"  # Falls back to content
        assert method == "content"
    
    def test_whitespace_only_header_ignored(self):
        """Test that whitespace-only header values are ignored."""
        js_content = "console.log('test');//# sourceMappingURL=content.js.map"
        js_url = "https://example.com/app.js"
        headers = {"SourceMap": "   \t\n"}  # Whitespace only
        
        url, method = detect_sourcemap_url(js_content, js_url, headers)
        
        assert url == "https://example.com/content.js.map"  # Falls back to content
        assert method == "content"
    
    def test_header_value_trimmed(self):
        """Test that header values are properly trimmed."""
        js_content = "console.log('test');"
        js_url = "https://example.com/app.js"
        headers = {"SourceMap": "  app.js.map  "}  # With whitespace
        
        url, method = detect_sourcemap_url(js_content, js_url, headers)
        
        assert url == "https://example.com/app.js.map"
        assert method == "header"
    
    def test_no_sourcemap_found(self):
        """Test conventional URL fallback when no explicit sourcemap found."""
        js_content = "console.log('test');"  # No comment
        js_url = "https://example.com/app.js"
        headers = {"Content-Type": "application/javascript"}  # No sourcemap headers
        
        url, method = detect_sourcemap_url(js_content, js_url, headers)
        
        # Should fallback to conventional URL (app.js -> app.js.map)
        assert url == "https://example.com/app.js.map"
        assert method == "content"  # Conventional fallback is part of content detection
    
    def test_multiple_sourcemap_headers_first_wins(self):
        """Test that when multiple headers exist, first valid one wins."""
        js_content = "console.log('test');"
        js_url = "https://example.com/app.js"
        headers = {
            "SourceMap": "first.js.map",
            "X-SourceMap": "second.js.map"
        }
        
        url, method = detect_sourcemap_url(js_content, js_url, headers)
        
        # Should get one of them (dict iteration order determines which)
        assert url in ["https://example.com/first.js.map", "https://example.com/second.js.map"]
        assert method == "header"
    
    def test_relative_path_resolution(self):
        """Test various relative path resolution scenarios."""
        js_content = "console.log('test');"
        base_cases = [
            # (js_url, header_value, expected_url)
            ("https://example.com/app.js", "app.js.map", "https://example.com/app.js.map"),
            ("https://example.com/js/app.js", "app.js.map", "https://example.com/js/app.js.map"),
            ("https://example.com/js/app.js", "../maps/app.js.map", "https://example.com/maps/app.js.map"),
            ("https://example.com/js/app.js", "./app.js.map", "https://example.com/js/app.js.map"),
            ("https://example.com/deep/nested/app.js", "../../maps/app.js.map", "https://example.com/maps/app.js.map"),
        ]
        
        for js_url, header_value, expected_url in base_cases:
            headers = {"SourceMap": header_value}
            url, method = detect_sourcemap_url(js_content, js_url, headers)
            assert url == expected_url, f"Failed for {js_url} + {header_value}, got {url}, expected {expected_url}"
            assert method == "header"
    
    def test_header_detection_with_port(self):
        """Test header detection works with URLs containing ports."""
        js_content = "console.log('test');"
        js_url = "https://localhost:3000/app.js"
        headers = {"SourceMap": "app.js.map"}
        
        url, method = detect_sourcemap_url(js_content, js_url, headers)
        
        assert url == "https://localhost:3000/app.js.map"
        assert method == "header"
    
    def test_no_conventional_fallback_for_non_js_url(self):
        """Test that non-.js URLs don't get conventional fallback."""
        js_content = "console.log('test');"  # No comment
        js_url = "https://example.com/app.txt"  # Not a .js file
        headers = {"Content-Type": "application/javascript"}  # No sourcemap headers
        
        url, method = detect_sourcemap_url(js_content, js_url, headers)
        
        assert url is None
        assert method == "none"


class TestSourceMapHeaderIntegration:
    """Integration tests for header hints in full ingestion flow."""
    
    # These would require more complex test setup with database
    # For now, focused on unit tests for the detection function
    pass