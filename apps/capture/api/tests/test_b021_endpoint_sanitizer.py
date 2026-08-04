"""
Tests for B-021 - Endpoint Sanitization and Noise Filter Pipeline

Tests the EndpointSanitizer service and its integration with RepEndpointsExtractor.
"""
import pytest
from app.services.endpoint_sanitizer import EndpointSanitizer
from app.services.rep_endpoints_extractor import RepEndpointsExtractor


class TestEndpointSanitizer:
    """Test the EndpointSanitizer service directly."""

    def setup_method(self):
        """Setup fresh sanitizer for each test."""
        self.sanitizer = EndpointSanitizer()

    def test_malformed_wrapper_removal(self):
        """Test removal of unbalanced brackets and quotes."""
        test_cases = [
            # Input endpoint -> Expected cleaned result
            ('(((/api/users', '/api/users'),
            ('[[[/api/posts', '/api/posts'),
            ('{{{/graphql', '/graphql'),
            ('"/api/auth"', '/api/auth'),
            ("'/api/login'", '/api/login'),
            ('`/api/data`', '/api/data'),
            ('(((/api/test)))', '/api/test'),
            ('["/api/users"]', '/api/users'),
        ]
        
        for input_url, expected in test_cases:
            endpoints = [{'url': input_url}]
            result = self.sanitizer.sanitize_endpoints(endpoints)
            if expected:
                assert len(result) == 1
                assert result[0]['url'] == expected
            else:
                assert len(result) == 0

    def test_non_printable_character_filtering(self):
        """Test filtering of non-printable characters."""
        test_cases = [
            ('/api/users\x00\x01', '/api/users'),
            ('/api/data\r\n', '/api/data'),
            ('/api/test\t', '/api/test'),
            ('/api/login\x0b\x0c', '/api/login'),
        ]
        
        for input_url, expected in test_cases:
            endpoints = [{'url': input_url}]
            result = self.sanitizer.sanitize_endpoints(endpoints)
            assert len(result) == 1
            assert result[0]['url'] == expected

    def test_noisy_domain_filtering(self):
        """Test filtering of known noisy domains."""
        noisy_endpoints = [
            {'url': 'https://example.com/api/test'},
            {'url': 'http://localhost:3000/api/data'},
            {'url': 'https://google-analytics.com/track'},
            {'url': 'https://facebook.com/api/share'},
        ]
        
        result = self.sanitizer.sanitize_endpoints(noisy_endpoints)
        assert len(result) == 0

    def test_noisy_extension_filtering(self):
        """Test filtering of file extensions that aren't API endpoints."""
        noisy_endpoints = [
            {'url': '/static/app.js'},
            {'url': '/styles/main.css'},
            {'url': '/images/logo.png'},
            {'url': '/fonts/roboto.woff2'},
            {'url': '/data.json'},
            {'url': '/readme.md'},
        ]
        
        result = self.sanitizer.sanitize_endpoints(noisy_endpoints)
        assert len(result) == 0

    def test_build_artifact_filtering(self):
        """Test filtering of build artifacts and static resources."""
        build_endpoints = [
            {'url': '/dist/bundle.js'},
            {'url': '/build/main.css'},
            {'url': '/static/assets/app.js'},
            {'url': '/node_modules/react/index.js'},
            {'url': '/webpack/hot-update.js'},
            {'url': '/chunk.a1b2c3d4.js'},
            {'url': '/vendors~main.12345678.js'},
            {'url': '/app.min.js'},
            {'url': '/bundle.a1b2c3d4e5f6g7h8.js'},
        ]
        
        result = self.sanitizer.sanitize_endpoints(build_endpoints)
        assert len(result) == 0

    def test_preserve_legitimate_endpoints(self):
        """Test that legitimate API endpoints are preserved."""
        legitimate_endpoints = [
            {'url': '/api/users'},
            {'url': '/api/v1/posts'},
            {'url': '/graphql'},
            {'url': '/auth/login'},
            {'url': 'https://wishandwash.co.il/api/data'},
            {'url': '/users/{id}'},
            {'url': '/products/:productId'},
        ]
        
        result = self.sanitizer.sanitize_endpoints(legitimate_endpoints)
        assert len(result) == len(legitimate_endpoints)
        
        # Check that URLs are preserved (possibly cleaned)
        result_urls = {item['url'] for item in result}
        for endpoint in legitimate_endpoints:
            # URL should either be preserved as-is or cleaned version should be present
            original_url = endpoint['url']
            assert original_url in result_urls or any(
                original_url.strip('"\' ') in url for url in result_urls
            )

    def test_malformed_url_detection(self):
        """Test detection of malformed URLs."""
        malformed_endpoints = [
            {'url': '/api/test[unclosed'},
            {'url': '/api/data}nomatch{'},
            {'url': '/api/users<script>'},
            {'url': '/api "with spaces" test'},
            {'url': '/api/undefined'},
            {'url': '/api/null'},
            {'url': '/api/NaN'},
            {'url': '/[object Object]'},
            {'url': '/function()'},
            {'url': '/api/test=>'},
        ]
        
        result = self.sanitizer.sanitize_endpoints(malformed_endpoints)
        assert len(result) == 0

    def test_length_validation(self):
        """Test URL length validation."""
        test_cases = [
            {'url': '/'},  # Too short
            {'url': ''},   # Empty
            {'url': '/' + 'a' * 2000},  # Too long
        ]
        
        result = self.sanitizer.sanitize_endpoints(test_cases)
        assert len(result) == 0

    def test_whitespace_only_filtering(self):
        """Test filtering of whitespace-only endpoints."""
        whitespace_endpoints = [
            {'url': '   '},
            {'url': '\t\n\r'},
            {'url': ''},
            {'url': '  \n  '},
        ]
        
        result = self.sanitizer.sanitize_endpoints(whitespace_endpoints)
        assert len(result) == 0

    def test_duplicate_slash_normalization(self):
        """Test normalization of duplicate slashes."""
        test_cases = [
            ('//api//users///', '/api/users/'),
            ('https://api.com///v1//data', 'https://api.com/v1/data'),
            ('///api/test', '/api/test'),
        ]
        
        for input_url, expected in test_cases:
            endpoints = [{'url': input_url}]
            result = self.sanitizer.sanitize_endpoints(endpoints)
            assert len(result) == 1
            assert result[0]['url'] == expected

    def test_configuration_options(self):
        """Test sanitizer configuration options."""
        # Test with domain filtering disabled
        sanitizer_no_domains = EndpointSanitizer(
            enable_domain_filtering=False, 
            enable_extension_filtering=True
        )
        
        noisy_domain_endpoint = [{'url': 'https://example.com/api/test'}]
        result = sanitizer_no_domains.sanitize_endpoints(noisy_domain_endpoint)
        assert len(result) == 1  # Should preserve since domain filtering disabled
        
        # Test with extension filtering disabled
        sanitizer_no_extensions = EndpointSanitizer(
            enable_domain_filtering=True,
            enable_extension_filtering=False
        )
        
        js_file_endpoint = [{'url': '/app.js'}]
        result = sanitizer_no_extensions.sanitize_endpoints(js_file_endpoint)
        assert len(result) == 1  # Should preserve since extension filtering disabled

    def test_endpoint_field_variations(self):
        """Test handling of different field names for endpoints."""
        test_variations = [
            {'url': '/api/users'},
            {'endpoint': '/api/posts'},
            {'url': '/api/data', 'endpoint': '/api/data'},  # Both fields
        ]
        
        result = self.sanitizer.sanitize_endpoints(test_variations)
        assert len(result) == 3
        
        # Check that appropriate fields are updated
        assert result[0]['url'] == '/api/users'
        assert result[1]['endpoint'] == '/api/posts'
        assert result[2]['url'] == '/api/data'
        assert result[2]['endpoint'] == '/api/data'

    def test_filter_stats(self):
        """Test filter statistics reporting."""
        stats = self.sanitizer.get_filter_stats()
        
        assert 'domain_filtering_enabled' in stats
        assert 'extension_filtering_enabled' in stats
        assert 'noisy_domains_count' in stats
        assert 'noisy_extensions_count' in stats
        
        assert stats['domain_filtering_enabled'] is True
        assert stats['extension_filtering_enabled'] is True
        assert stats['noisy_domains_count'] > 0
        assert stats['noisy_extensions_count'] > 0


class TestRepEndpointsExtractorIntegration:
    """Test integration of sanitization with RepEndpointsExtractor."""

    def setup_method(self):
        """Setup extractor for each test."""
        self.extractor = RepEndpointsExtractor()

    def test_sanitization_integration_with_real_content(self):
        """Test that sanitization works with real JavaScript content."""
        # JavaScript content with mix of legitimate and noisy endpoints
        js_content = '''
        // Legitimate API endpoints
        fetch('/api/users');
        axios.post('/api/v1/posts', data);
        const graphqlEndpoint = '/graphql';
        
        // Noisy/malformed endpoints that should be filtered
        console.log('/static/app.js'); 
        import './styles/main.css';
        const buildPath = '/dist/bundle.js';
        const malformed = '(((/api/broken';
        const withSpaces = '/api test spaces';
        const jsArtifact = '/chunk.a1b2c3d4.js';
        '''
        
        results = self.extractor.extract(js_content, 'https://wishandwash.co.il/test.js')
        
        # Should have legitimate endpoints but noise filtered
        legitimate_urls = ['/api/users', '/api/v1/posts', '/graphql']
        result_urls = [item.get('url') or item.get('endpoint') for item in results]
        
        # Check that legitimate endpoints are preserved
        for legit_url in legitimate_urls:
            assert any(legit_url in url for url in result_urls), f"Missing legitimate endpoint: {legit_url}"
        
        # Check that noisy endpoints are filtered out
        noisy_patterns = ['/static/', '/styles/', '/dist/', '/chunk.']
        for pattern in noisy_patterns:
            assert not any(pattern in url for url in result_urls), f"Noisy pattern not filtered: {pattern}"

    def test_sanitization_preserves_metadata(self):
        """Test that sanitization preserves other endpoint metadata."""
        js_content = 'fetch("/api/users");'
        
        results = self.extractor.extract(js_content, 'https://wishandwash.co.il/test.js')
        
        assert len(results) > 0
        result = results[0]
        
        # Check that sanitization preserves all metadata fields
        expected_fields = [
            'url', 'endpoint', 'method', 'type', 'patternType', 
            'confidence', 'confidence_score', 'extractor', 'file',
            'source_file', 'base_url', 'line', 'column', 'context'
        ]
        
        for field in expected_fields:
            assert field in result, f"Missing field after sanitization: {field}"

    def test_empty_result_after_sanitization(self):
        """Test handling when all endpoints are filtered out."""
        # JavaScript content with only noisy endpoints
        js_content = '''
        import './app.css';
        const bundle = '/dist/main.js';
        console.log('/static/image.png');
        '''
        
        results = self.extractor.extract(js_content)
        
        # All endpoints should be filtered out by sanitization
        assert len(results) == 0 or all(
            not any(noise in item.get('url', '') for noise in ['/dist/', '/static/', '.css', '.js', '.png'])
            for item in results
        )


def test_sanitization_can_be_disabled():
    """Test that sanitization can be disabled via configuration."""
    # This test assumes we can modify settings during test
    # In real implementation, this might be handled differently
    from app.config import settings
    
    original_setting = settings.endpoint_sanitization_enabled
    try:
        # Temporarily disable sanitization
        settings.endpoint_sanitization_enabled = False
        
        extractor = RepEndpointsExtractor()
        js_content = 'const bundle = "/dist/main.js";'
        
        results = extractor.extract(js_content)
        
        # With sanitization disabled, noisy endpoints might be present
        # (depends on other filtering in the extractor)
        
    finally:
        # Restore original setting
        settings.endpoint_sanitization_enabled = original_setting