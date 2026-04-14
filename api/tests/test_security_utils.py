"""
Comprehensive unit tests for security utilities.
"""
import pytest
import tempfile
import os
import subprocess
from unittest.mock import patch, mock_open

from app.services.security_utils import (
    SecurityValidator,
    SecureSubprocess, 
    SecureTempFile,
    SecurityConfig
)


class TestSecurityValidator:
    """Test SecurityValidator class."""
    
    def test_validate_js_content_valid(self):
        """Test valid JavaScript content validation."""
        content = "function hello() { return 'world'; }"
        result = SecurityValidator.validate_js_content(content)
        assert result == content
    
    def test_validate_js_content_empty(self):
        """Test empty content validation."""
        with pytest.raises(ValueError, match="Content cannot be empty"):
            SecurityValidator.validate_js_content("")
    
    def test_validate_js_content_not_string(self):
        """Test non-string content validation."""
        with pytest.raises(ValueError, match="Content must be a string"):
            SecurityValidator.validate_js_content(123)
    
    def test_validate_js_content_too_large(self):
        """Test oversized content validation."""
        large_content = "x" * (SecurityValidator.MAX_JS_CONTENT_SIZE + 1)
        with pytest.raises(ValueError, match="Content too large"):
            SecurityValidator.validate_js_content(large_content)
    
    def test_validate_js_content_binary_data(self):
        """Test binary data rejection."""
        content = "function test() {\x00 return true; }"
        with pytest.raises(ValueError, match="Content contains null bytes"):
            SecurityValidator.validate_js_content(content)
    
    def test_validate_url_valid_http(self):
        """Test valid HTTP URL validation."""
        url = "http://example.com/api/data"
        result = SecurityValidator.validate_url(url)
        assert result == url
    
    def test_validate_url_valid_https(self):
        """Test valid HTTPS URL validation."""
        url = "https://api.example.com/v1/users"
        result = SecurityValidator.validate_url(url)
        assert result == url
    
    def test_validate_url_invalid_scheme(self):
        """Test invalid URL scheme rejection."""
        with pytest.raises(ValueError, match="Only HTTP/HTTPS URLs are allowed"):
            SecurityValidator.validate_url("ftp://example.com/file")
    
    def test_validate_url_dangerous_patterns(self):
        """Test dangerous pattern detection in URLs."""
        dangerous_urls = [
            "http://example.com/api?cmd=ls;rm -rf /",
            "https://example.com/test?path=../../../etc/passwd",
            "http://example.com/api?exec=`cat /etc/passwd`"
        ]
        
        for url in dangerous_urls:
            with pytest.raises(ValueError, match="potentially dangerous pattern"):
                SecurityValidator.validate_url(url)
    
    def test_validate_url_too_long(self):
        """Test URL length validation."""
        long_url = "http://example.com/" + "x" * SecurityValidator.MAX_URL_LENGTH
        with pytest.raises(ValueError, match="URL too long"):
            SecurityValidator.validate_url(long_url)
    
    def test_validate_file_path_valid(self):
        """Test valid file path validation."""
        path = "src/components/App.js"
        result = SecurityValidator.validate_file_path(path)
        assert result == path
    
    def test_validate_file_path_traversal(self):
        """Test path traversal detection."""
        with pytest.raises(ValueError, match="Path traversal detected"):
            SecurityValidator.validate_file_path("../../../etc/passwd")
    
    def test_validate_file_path_absolute(self):
        """Test absolute path rejection."""
        with pytest.raises(ValueError, match="Absolute paths not allowed"):
            SecurityValidator.validate_file_path("/etc/passwd")
    
    def test_sanitize_filename_valid(self):
        """Test filename sanitization."""
        filename = "test-file_123.js"
        result = SecurityValidator.sanitize_filename(filename)
        assert result == filename
    
    def test_sanitize_filename_dangerous_chars(self):
        """Test dangerous character removal."""
        filename = "test;rm -rf /.js"
        result = SecurityValidator.sanitize_filename(filename)
        assert ";" not in result
        assert "rm" not in result
        assert result.endswith(".js")
    
    def test_sanitize_filename_directory_traversal(self):
        """Test directory component removal."""
        filename = "../../../malicious.js"
        result = SecurityValidator.sanitize_filename(filename)
        assert not result.startswith("../")
        assert "malicious.js" in result or result.endswith(".js")


class TestSecureSubprocess:
    """Test SecureSubprocess class."""
    
    def test_run_command_valid(self):
        """Test valid command execution."""
        result = SecureSubprocess.run_command(['echo', 'hello'])
        assert result['success'] is True
        assert 'hello' in result['stdout']
        assert result['returncode'] == 0
    
    def test_run_command_nonexistent_binary(self):
        """Test nonexistent binary handling."""
        with pytest.raises(ValueError, match="Binary not found"):
            SecureSubprocess.run_command(['/nonexistent/binary'])
    
    def test_run_command_dangerous_args(self):
        """Test dangerous argument detection."""
        with pytest.raises(ValueError, match="dangerous pattern"):
            SecureSubprocess.run_command(['echo', 'test; rm -rf /'])
    
    def test_run_command_timeout(self):
        """Test command timeout handling."""
        result = SecureSubprocess.run_command(['sleep', '10'], timeout=1)
        assert result['success'] is False
        assert result['timeout'] is True
        assert 'timed out' in result['stderr']
    
    def test_run_command_empty_command(self):
        """Test empty command rejection."""
        with pytest.raises(ValueError, match="Command must be a non-empty list"):
            SecureSubprocess.run_command([])
    
    def test_run_command_non_string_args(self):
        """Test non-string argument rejection."""
        with pytest.raises(ValueError, match="All command arguments must be strings"):
            SecureSubprocess.run_command(['echo', 123])
    
    @patch('subprocess.run')
    def test_run_command_large_output(self, mock_run):
        """Test large output handling."""
        # Mock large output
        large_output = "x" * (SecureSubprocess.MAX_OUTPUT_SIZE + 1)
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = large_output
        mock_run.return_value.stderr = ""
        
        with pytest.raises(ValueError, match="Command output too large"):
            SecureSubprocess.run_command(['echo', 'test'])


class TestSecureTempFile:
    """Test SecureTempFile class."""
    
    def test_create_secure_temp_file(self):
        """Test secure temporary file creation."""
        content = "function test() { return 'hello'; }"
        
        with SecureTempFile.create_secure_temp_file(content) as temp_file:
            assert os.path.exists(temp_file.file_path)
            
            # Check file permissions (owner read/write only)
            stat_info = os.stat(temp_file.file_path)
            assert oct(stat_info.st_mode)[-3:] == '600'
            
            # Verify content
            with open(temp_file.file_path, 'r') as f:
                assert f.read() == content
        
        # File should be cleaned up
        assert not os.path.exists(temp_file.file_path)
    
    def test_create_temp_file_invalid_content(self):
        """Test temp file creation with invalid content."""
        with pytest.raises(ValueError):
            SecureTempFile.create_secure_temp_file("")
    
    def test_cleanup_multiple_calls(self):
        """Test multiple cleanup calls don't error."""
        content = "function test() { return 'hello'; }"
        
        temp_file = SecureTempFile.create_secure_temp_file(content)
        path = temp_file.file_path
        
        # Cleanup multiple times
        temp_file.cleanup()
        temp_file.cleanup()
        temp_file.cleanup()
        
        assert not os.path.exists(path)
    
    def test_context_manager_exception_cleanup(self):
        """Test cleanup on exception in context manager."""
        content = "function test() { return 'hello'; }"
        path = None
        
        try:
            with SecureTempFile.create_secure_temp_file(content) as temp_file:
                path = temp_file.file_path
                assert os.path.exists(path)
                raise Exception("Test exception")
        except Exception:
            pass
        
        # File should still be cleaned up
        assert not os.path.exists(path)


class TestSecurityConfig:
    """Test SecurityConfig class."""
    
    def test_sanitize_for_logging_api_key(self):
        """Test API key sanitization in logs."""
        data = "Config: api_key='sk_live_1234567890abcdef'"
        result = SecurityConfig.sanitize_for_logging(data)
        assert 'sk_live_1234567890abcdef' not in result
        assert 'REDACTED' in result
    
    def test_sanitize_for_logging_password(self):
        """Test password sanitization in logs."""
        data = "Login: password=secretpassword123"
        result = SecurityConfig.sanitize_for_logging(data)
        assert 'secretpassword123' not in result
        assert 'REDACTED' in result
    
    def test_sanitize_for_logging_token(self):
        """Test token sanitization in logs."""
        data = 'Authorization: bearer="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"'
        result = SecurityConfig.sanitize_for_logging(data)
        assert 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9' not in result
        assert 'REDACTED' in result
    
    def test_sanitize_for_logging_multiple_patterns(self):
        """Test multiple pattern sanitization."""
        data = "api_key=test123 password='secret' token=abc123"
        result = SecurityConfig.sanitize_for_logging(data)
        assert 'test123' not in result
        assert 'secret' not in result
        assert 'abc123' not in result
        assert result.count('REDACTED') == 3
    
    def test_sanitize_for_logging_case_insensitive(self):
        """Test case-insensitive pattern matching."""
        data = "API_KEY=test123 PASSWORD='secret' TOKEN=abc123"
        result = SecurityConfig.sanitize_for_logging(data)
        assert 'test123' not in result
        assert 'secret' not in result
        assert 'abc123' not in result


# Integration test fixtures
@pytest.fixture
def temp_js_file():
    """Create a temporary JavaScript file for testing."""
    content = """
    function fetchData() {
        fetch('/api/users');
        var api_key = 'sk_live_1234567890abcdef';
        return axios.get('https://api.example.com/data');
    }
    """
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
        f.write(content)
        f.flush()
        yield f.name
    
    # Cleanup
    try:
        os.unlink(f.name)
    except:
        pass


@pytest.fixture
def mock_jsluice_binary():
    """Create a mock jsluice binary for testing."""
    # Create a temporary script that acts like jsluice
    script_content = '''#!/bin/bash
    if [ "$1" = "urls" ]; then
        echo '{"url": "/api/users", "line": 3, "column": 8, "source": "fetch", "context": "fetch(\\\"/api/users\\\")"}'
    elif [ "$1" = "secrets" ]; then
        echo '{"match": "sk_live_1234567890abcdef", "rule": "stripe_secret", "line": 4, "confidence": "high"}'
    elif [ "$1" = "--help" ]; then
        echo "jsluice - JavaScript analysis tool"
    fi
    '''
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
        f.write(script_content)
        f.flush()
        os.chmod(f.name, 0o755)
        yield f.name
    
    # Cleanup
    try:
        os.unlink(f.name)
    except:
        pass


class TestSecurityIntegration:
    """Integration tests for security components."""
    
    def test_end_to_end_validation(self, temp_js_file):
        """Test complete validation workflow."""
        # Read file content
        with open(temp_js_file, 'r') as f:
            content = f.read()
        
        # Validate content
        validated_content = SecurityValidator.validate_js_content(content)
        assert validated_content == content
        
        # Create secure temp file
        with SecureTempFile.create_secure_temp_file(validated_content) as temp_file:
            # Verify file exists and is secure
            assert os.path.exists(temp_file.file_path)
            stat_info = os.stat(temp_file.file_path)
            assert oct(stat_info.st_mode)[-3:] == '600'
        
        # Verify cleanup
        assert not os.path.exists(temp_file.file_path)
    
    def test_command_injection_prevention(self):
        """Test command injection prevention."""
        dangerous_content = "'; rm -rf /; echo '"
        
        # Content should be validated and accepted (it's valid JS as string)
        validated = SecurityValidator.validate_js_content(dangerous_content)
        assert validated == dangerous_content
        
        # But using it as a command argument should fail
        with pytest.raises(ValueError):
            SecureSubprocess.run_command(['echo', dangerous_content])
    
    def test_path_traversal_prevention(self):
        """Test path traversal attack prevention."""
        malicious_paths = [
            "../../../etc/passwd",
            "..\\..\\..\\windows\\system32\\config\\sam",
            "/etc/shadow",
            "C:\\Windows\\System32\\config\\SAM"
        ]
        
        for path in malicious_paths:
            with pytest.raises(ValueError):
                SecurityValidator.validate_file_path(path)