"""
Comprehensive unit tests for secure jsluice extractor.
"""
import pytest
import json
from unittest.mock import patch, MagicMock
import tempfile
import os

from app.services.jsluice_extractor_secure import SecureJSluiceExtractor
from app.services.security_utils import SecurityValidator


class TestSecureJSluiceExtractor:
    """Test SecureJSluiceExtractor class."""
    
    @pytest.fixture
    def mock_jsluice_binary(self):
        """Create a mock jsluice binary."""
        # Create a simple shell script that mimics jsluice behavior
        script_content = '''#!/bin/bash
        if [ "$1" = "--help" ]; then
            echo "jsluice - JavaScript analysis tool"
            exit 0
        elif [ "$1" = "urls" ]; then
            # Output mock URL results
            echo '{"url": "/api/users", "line": 1, "column": 5, "source": "fetch", "context": "fetch api users"}'
            echo '{"url": "https://api.example.com/data", "line": 2, "column": 10, "source": "axios", "context": "axios api data"}'
            exit 0
        elif [ "$1" = "secrets" ]; then
            # Output mock secret results
            echo '{"match": "sk_live_1234567890abcdef", "rule": "stripe_secret", "line": 3, "confidence": "high", "context": "stripe key"}'
            echo '{"match": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", "rule": "jwt_token", "line": 4, "confidence": "medium", "context": "jwt token"}'
            exit 0
        fi
        exit 1
        '''
        
        fd, path = tempfile.mkstemp(suffix='.sh')
        with os.fdopen(fd, 'w') as f:
            f.write(script_content)
        os.chmod(path, 0o755)
        yield path
        
        # Cleanup
        try:
            os.unlink(path)
        except:
            pass
    
    @pytest.fixture
    def extractor(self, mock_jsluice_binary):
        """Create SecureJSluiceExtractor with mock binary."""
        return SecureJSluiceExtractor(mock_jsluice_binary)
    
    @pytest.fixture
    def sample_js_content(self):
        """Sample JavaScript content for testing."""
        return """
        function fetchUserData() {
            fetch('/api/users');
            axios.get('https://api.example.com/data');
            var api_key = 'sk_live_1234567890abcdef';
            var token = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9';
            return userData;
        }
        """
    
    def test_initialization_valid_binary(self, mock_jsluice_binary):
        """Test extractor initialization with valid binary."""
        extractor = SecureJSluiceExtractor(mock_jsluice_binary)
        assert extractor.jsluice_binary == mock_jsluice_binary
        assert extractor._stats['urls_extracted'] == 0
    
    def test_initialization_invalid_binary_path(self):
        """Test extractor initialization with invalid binary path."""
        with pytest.raises(ValueError, match="must be in approved directory"):
            SecureJSluiceExtractor("/tmp/malicious_binary")
    
    def test_initialization_nonexistent_binary(self):
        """Test extractor initialization with nonexistent binary."""
        with pytest.raises(RuntimeError, match="Binary not found"):
            SecureJSluiceExtractor("/usr/local/bin/nonexistent_jsluice")
    
    def test_extract_urls_valid_content(self, extractor, sample_js_content):
        """Test URL extraction with valid content."""
        result = extractor.extract_urls(sample_js_content)
        
        assert isinstance(result, list)
        assert len(result) >= 1
        
        # Check first URL result
        url_result = result[0]
        assert 'url' in url_result
        assert 'type' in url_result
        assert url_result['type'] == 'jsluice_url'
        assert url_result['confidence'] == 'high'
        assert url_result['extractor'] == 'jsluice'
    
    def test_extract_urls_with_base_url(self, extractor, sample_js_content):
        """Test URL extraction with base URL resolution."""
        base_url = "https://example.com"
        result = extractor.extract_urls(sample_js_content, base_url=base_url)
        
        assert isinstance(result, list)
        # Should have called jsluice with --resolve flag
    
    def test_extract_urls_invalid_content(self, extractor):
        """Test URL extraction with invalid content."""
        with pytest.raises(ValueError, match="Content cannot be empty"):
            extractor.extract_urls("")
    
    def test_extract_urls_malicious_base_url(self, extractor, sample_js_content):
        """Test URL extraction with malicious base URL."""
        malicious_url = "http://example.com'; rm -rf /; echo '"
        
        with pytest.raises(ValueError, match="potentially dangerous pattern"):
            extractor.extract_urls(sample_js_content, base_url=malicious_url)
    
    def test_extract_secrets_valid_content(self, extractor, sample_js_content):
        """Test secret extraction with valid content."""
        result = extractor.extract_secrets(sample_js_content)
        
        assert isinstance(result, list)
        assert len(result) >= 1
        
        # Check secret result structure
        secret_result = result[0]
        assert 'value' in secret_result
        assert 'type' in secret_result
        assert 'confidence' in secret_result
        assert 'extractor' in secret_result
        assert secret_result['extractor'] == 'jsluice'
        assert 'security_classification' in secret_result
    
    def test_extract_secrets_with_custom_patterns(self, extractor, sample_js_content):
        """Test secret extraction with custom patterns file."""
        # Create a temporary patterns file
        patterns_content = '''
        rules:
          - name: custom_api_key
            pattern: "custom_key_[a-zA-Z0-9]{16}"
            confidence: high
        '''
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
            f.write(patterns_content)
            f.flush()
            patterns_file = f.name
        
        try:
            result = extractor.extract_secrets(sample_js_content, 
                                            custom_patterns_file=patterns_file)
            assert isinstance(result, list)
        finally:
            os.unlink(patterns_file)
    
    def test_extract_secrets_invalid_patterns_file(self, extractor, sample_js_content):
        """Test secret extraction with invalid patterns file."""
        with pytest.raises(ValueError, match="Patterns file not found"):
            extractor.extract_secrets(sample_js_content, 
                                    custom_patterns_file="/nonexistent/file.yml")
    
    def test_extract_comprehensive_valid_content(self, extractor, sample_js_content):
        """Test comprehensive extraction."""
        result = extractor.extract_comprehensive(sample_js_content)
        
        assert result['success'] is True
        assert 'analysis' in result
        assert 'urls' in result['analysis']
        assert 'secrets' in result['analysis']
        assert 'stats' in result
        assert 'metadata' in result
        assert 'extractor_stats' in result
        
        # Check stats
        stats = result['stats']
        assert 'urls_found' in stats
        assert 'secrets_found' in stats
        assert 'processing_time_ms' in stats
        assert 'content_size_bytes' in stats
    
    def test_extract_comprehensive_with_options(self, extractor, sample_js_content):
        """Test comprehensive extraction with options."""
        options = {
            'resolve_urls': True,
            'include_source': True,
            'unique': True,
            'timeout': 45
        }
        
        base_url = "https://example.com"
        result = extractor.extract_comprehensive(sample_js_content, 
                                               base_url=base_url, 
                                               options=options)
        
        assert result['success'] is True
        assert result['metadata']['base_url'] == base_url
        assert result['metadata']['options'] == options
    
    def test_secret_classification_critical(self, extractor):
        """Test critical secret classification."""
        secret = {
            'type': 'private_key',
            'value': 'sk_live_abcd1234567890'
        }
        
        classification = extractor._classify_secret(secret)
        assert classification == 'critical'
    
    def test_secret_classification_high(self, extractor):
        """Test high secret classification."""
        secret = {
            'type': 'api_key',
            'value': 'api_key_12345'
        }
        
        classification = extractor._classify_secret(secret)
        assert classification == 'high'
    
    def test_secret_classification_medium(self, extractor):
        """Test medium secret classification."""
        secret = {
            'type': 'password',
            'value': 'mypassword123'
        }
        
        classification = extractor._classify_secret(secret)
        assert classification == 'medium'
    
    def test_secret_classification_low(self, extractor):
        """Test low secret classification."""
        secret = {
            'type': 'unknown',
            'value': 'somevalue'
        }
        
        classification = extractor._classify_secret(secret)
        assert classification == 'low'
    
    def test_false_positive_filtering(self, extractor):
        """Test false positive secret filtering."""
        # Mock jsluice output with false positives
        mock_secrets = [
            {'value': 'test123', 'type': 'api_key', 'confidence': 'medium'},
            {'value': 'example_key', 'type': 'api_key', 'confidence': 'medium'},
            {'value': 'sk_live_real_key_123', 'type': 'stripe_secret', 'confidence': 'high'},
            {'value': '***', 'type': 'password', 'confidence': 'low'},
            {'value': 'ab', 'type': 'token', 'confidence': 'low'},  # Too short
        ]
        
        filtered = extractor._filter_and_sanitize_secrets(mock_secrets)
        
        # Should filter out test123, example_key, ***, and ab
        # Should keep sk_live_real_key_123
        assert len(filtered) == 1
        assert filtered[0]['value'] == 'sk_live_real_key_123'
    
    def test_confidence_mapping(self, extractor):
        """Test jsluice confidence mapping."""
        test_cases = [
            ('high', 'high'),
            ('medium', 'medium'),
            ('low', 'low'),
            ('info', 'low'),
            ('unknown', 'low'),
            ('HIGH', 'high'),  # Case insensitive
        ]
        
        for jsluice_conf, expected in test_cases:
            result = extractor._map_jsluice_confidence(jsluice_conf)
            assert result == expected
    
    def test_stats_tracking(self, extractor, sample_js_content):
        """Test statistics tracking."""
        initial_stats = extractor.get_stats()
        assert initial_stats['urls_extracted'] == 0
        assert initial_stats['secrets_extracted'] == 0
        
        # Extract URLs and secrets
        extractor.extract_urls(sample_js_content)
        extractor.extract_secrets(sample_js_content)
        
        updated_stats = extractor.get_stats()
        assert updated_stats['urls_extracted'] > 0
        assert updated_stats['secrets_extracted'] > 0
        assert updated_stats['last_extraction'] is not None
    
    def test_stats_reset(self, extractor, sample_js_content):
        """Test statistics reset functionality."""
        # Generate some stats
        extractor.extract_urls(sample_js_content)
        
        assert extractor.get_stats()['urls_extracted'] > 0
        
        # Reset stats
        extractor.reset_stats()
        
        stats = extractor.get_stats()
        assert stats['urls_extracted'] == 0
        assert stats['secrets_extracted'] == 0
        assert stats['errors_encountered'] == 0
        assert stats['last_extraction'] is None
    
    @patch('app.services.security_utils.SecureSubprocess.run_command')
    def test_jsluice_command_failure(self, mock_run, extractor, sample_js_content):
        """Test handling of jsluice command failures."""
        # Mock command failure
        mock_run.return_value = {
            'success': False,
            'returncode': 1,
            'stdout': '',
            'stderr': 'jsluice: error processing file',
            'timeout': False
        }
        
        with pytest.raises(RuntimeError, match="jsluice URL extraction failed"):
            extractor.extract_urls(sample_js_content)
        
        # Check error stats
        stats = extractor.get_stats()
        assert stats['errors_encountered'] > 0
    
    @patch('app.services.security_utils.SecureSubprocess.run_command')
    def test_jsluice_timeout_handling(self, mock_run, extractor, sample_js_content):
        """Test handling of jsluice timeout."""
        # Mock timeout
        mock_run.return_value = {
            'success': False,
            'returncode': -1,
            'stdout': '',
            'stderr': 'Command timed out after 30s',
            'timeout': True
        }
        
        with pytest.raises(RuntimeError, match="URL extraction failed"):
            extractor.extract_urls(sample_js_content)
    
    def test_malformed_jsluice_output(self, extractor):
        """Test handling of malformed jsluice output."""
        malformed_output = '''
        {"url": "/api/users", "line": 1}
        {invalid json}
        {"url": "https://example.com"}
        '''
        
        results = extractor._parse_jsluice_output(malformed_output, 'url')
        
        # Should skip malformed lines and process valid ones
        assert len(results) >= 1
        assert results[0]['url'] in ['/api/users', 'https://example.com']
    
    def test_url_validation_in_results(self, extractor):
        """Test URL validation in parsed results."""
        # Mock output with valid and invalid URLs
        jsluice_output = '''
        {"url": "https://example.com/api", "line": 1, "source": "fetch"}
        {"url": "javascript:alert(1)", "line": 2, "source": "location"}
        {"url": "/api/users", "line": 3, "source": "fetch"}
        '''
        
        results = extractor._parse_jsluice_output(jsluice_output, 'url')
        
        # Should filter out javascript: URL and keep valid ones
        valid_urls = [r['url'] for r in results]
        assert 'javascript:alert(1)' not in valid_urls
        assert any(url in ['https://example.com/api', '/api/users'] for url in valid_urls)
    
    def test_secret_value_size_limiting(self, extractor):
        """Test secret value size limiting for security."""
        # Mock secret with very long value
        long_value = 'x' * 200
        secret_data = {
            'match': long_value,
            'rule': 'test_secret',
            'line': 1,
            'confidence': 'high'
        }
        
        result = extractor._parse_secret_result(secret_data, 1)
        
        # Value should be truncated
        assert len(result['value']) <= 100
    
    def test_context_size_limiting(self, extractor):
        """Test context size limiting for memory efficiency."""
        long_context = 'x' * 300
        url_data = {
            'url': 'https://example.com',
            'line': 1,
            'context': long_context
        }
        
        result = extractor._parse_url_result(url_data, 1)
        
        # Context should be truncated
        assert len(result['context']) <= 200


class TestJSluiceExtractorEdgeCases:
    """Test edge cases and error conditions."""
    
    def test_very_large_js_content(self, mock_jsluice_binary):
        """Test handling of very large JavaScript content."""
        extractor = SecureJSluiceExtractor(mock_jsluice_binary)
        
        # Create content larger than the security limit
        large_content = "console.log('x');" * 1000000
        
        with pytest.raises(ValueError, match="Content too large"):
            extractor.extract_urls(large_content)
    
    def test_binary_content_rejection(self, mock_jsluice_binary):
        """Test rejection of binary content."""
        extractor = SecureJSluiceExtractor(mock_jsluice_binary)
        
        binary_content = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'.decode('latin1')
        
        with pytest.raises(ValueError, match="Content contains null bytes"):
            extractor.extract_urls(binary_content)
    
    def test_unicode_content_handling(self, mock_jsluice_binary):
        """Test handling of Unicode content."""
        extractor = SecureJSluiceExtractor(mock_jsluice_binary)
        
        unicode_content = """
        // 这是一个JavaScript函数
        function 测试() {
            fetch('/api/用户');
            console.log('Hello 🌍');
        }
        """
        
        # Should handle Unicode content without errors
        result = extractor.extract_urls(unicode_content)
        assert isinstance(result, list)
    
    def test_empty_jsluice_output(self, mock_jsluice_binary):
        """Test handling of empty jsluice output."""
        extractor = SecureJSluiceExtractor(mock_jsluice_binary)
        
        # Mock empty output
        with patch('app.services.security_utils.SecureSubprocess.run_command') as mock_run:
            mock_run.return_value = {
                'success': True,
                'returncode': 0,
                'stdout': '',
                'stderr': '',
                'timeout': False
            }
            
            result = extractor.extract_urls("function test() {}")
            assert result == []
    
    def test_network_timeout_simulation(self, mock_jsluice_binary):
        """Test network timeout simulation."""
        extractor = SecureJSluiceExtractor(mock_jsluice_binary)
        
        # Test with very short timeout
        options = {'timeout': 1}
        
        # This should work since we're using a mock binary
        # In real scenario, this would test timeout handling
        result = extractor.extract_urls("function test() {}", options=options)
        assert isinstance(result, list)