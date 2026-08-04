"""
Security utilities for input validation and safe operations.
"""
import re
import os
import tempfile
import subprocess
import logging
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlparse
import secrets
import string

logger = logging.getLogger(__name__)

class SecurityValidator:
    """Centralized security validation utilities."""
    
    # Size limits (configurable)
    MAX_JS_CONTENT_SIZE = 10 * 1024 * 1024  # 10MB
    MAX_URL_LENGTH = 2048
    MAX_FILENAME_LENGTH = 255
    
    # Allowed file extensions
    ALLOWED_JS_EXTENSIONS = {'.js', '.mjs', '.jsx', '.ts', '.tsx'}
    
    # URL validation patterns
    VALID_URL_PATTERN = re.compile(
        r'^https?://'  # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain
        r'localhost|'  # localhost
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # IP
        r'(?::\d+)?'  # optional port
        r'(?:/?|[/?]\S+)$', re.IGNORECASE
    )
    
    # Command injection patterns to block
    DANGEROUS_PATTERNS = [
        r'[;&|`$(){}[\]<>]',  # Shell metacharacters
        r'\.\./',             # Path traversal
        r'/etc/',             # System paths
        r'/proc/',            # System paths
        r'/dev/',             # System paths
    ]
    
    @classmethod
    def validate_js_content(cls, content: str) -> str:
        """
        Validate JavaScript content for safety.
        
        Args:
            content: JavaScript source code
            
        Returns:
            Validated content
            
        Raises:
            ValueError: If content is invalid or dangerous
        """
        if not isinstance(content, str):
            raise ValueError("Content must be a string")
        
        if len(content) == 0:
            raise ValueError("Content cannot be empty")
        
        if len(content) > cls.MAX_JS_CONTENT_SIZE:
            raise ValueError(f"Content too large: {len(content)} bytes (max: {cls.MAX_JS_CONTENT_SIZE})")
        
        # Check for null bytes (binary data)
        if '\x00' in content:
            raise ValueError("Content contains null bytes (binary data not allowed)")
        
        # Basic sanity check - should contain some JavaScript-like patterns
        if len(content.strip()) < 10 and not any(keyword in content for keyword in 
            ['function', 'var', 'let', 'const', 'class', 'import', 'export', 'require']):
            logger.warning("Content doesn't look like JavaScript")
        
        return content
    
    @classmethod
    def validate_url(cls, url: str) -> str:
        """
        Validate URL for safety.
        
        Args:
            url: URL string to validate
            
        Returns:
            Validated URL
            
        Raises:
            ValueError: If URL is invalid or dangerous
        """
        if not isinstance(url, str):
            raise ValueError("URL must be a string")
        
        if len(url) == 0:
            raise ValueError("URL cannot be empty")
        
        if len(url) > cls.MAX_URL_LENGTH:
            raise ValueError(f"URL too long: {len(url)} chars (max: {cls.MAX_URL_LENGTH})")
        
        # Check for dangerous patterns
        for pattern in cls.DANGEROUS_PATTERNS:
            if re.search(pattern, url):
                raise ValueError(f"URL contains potentially dangerous pattern: {pattern}")
        
        # Validate URL format
        if not cls.VALID_URL_PATTERN.match(url):
            raise ValueError("Invalid URL format")
        
        # Parse and validate components
        try:
            parsed = urlparse(url)
            if parsed.scheme not in ('http', 'https'):
                raise ValueError("Only HTTP/HTTPS URLs are allowed")
            
            if not parsed.netloc:
                raise ValueError("URL must have a valid domain")
                
        except Exception as e:
            raise ValueError(f"Invalid URL: {e}")
        
        return url
    
    @classmethod
    def validate_file_path(cls, path: str) -> str:
        """
        Validate file path for safety.
        
        Args:
            path: File path to validate
            
        Returns:
            Validated path
            
        Raises:
            ValueError: If path is invalid or dangerous
        """
        if not isinstance(path, str):
            raise ValueError("Path must be a string")
        
        if len(path) == 0:
            raise ValueError("Path cannot be empty")
        
        if len(path) > cls.MAX_FILENAME_LENGTH:
            raise ValueError(f"Path too long: {len(path)} chars")
        
        # Check for path traversal
        if '..' in path:
            raise ValueError("Path traversal detected")
        
        # Check for absolute paths (should be relative)
        if os.path.isabs(path):
            raise ValueError("Absolute paths not allowed")
        
        # Validate extension if it's a file
        if '.' in os.path.basename(path):
            ext = os.path.splitext(path)[1].lower()
            if ext and ext not in cls.ALLOWED_JS_EXTENSIONS and ext != '.map':
                logger.warning(f"Unusual file extension: {ext}")
        
        return path
    
    @classmethod
    def sanitize_filename(cls, filename: str) -> str:
        """
        Sanitize filename for safe storage.
        
        Args:
            filename: Original filename
            
        Returns:
            Sanitized filename
        """
        # Remove directory components
        filename = os.path.basename(filename)
        
        # Replace dangerous characters
        safe_chars = string.ascii_letters + string.digits + '._-'
        sanitized = ''.join(c if c in safe_chars else '_' for c in filename)
        
        # Ensure reasonable length
        if len(sanitized) > cls.MAX_FILENAME_LENGTH:
            name, ext = os.path.splitext(sanitized)
            max_name_len = cls.MAX_FILENAME_LENGTH - len(ext) - 8  # Reserve space for uniqueness
            sanitized = name[:max_name_len] + '_' + secrets.token_hex(4) + ext
        
        # Ensure not empty
        if not sanitized or sanitized.startswith('.'):
            sanitized = 'file_' + secrets.token_hex(4) + '.js'
        
        return sanitized


class SecureSubprocess:
    """Secure wrapper for subprocess operations."""
    
    DEFAULT_TIMEOUT = 30  # seconds
    MAX_OUTPUT_SIZE = 50 * 1024 * 1024  # 50MB
    
    @classmethod
    def run_command(cls, cmd: List[str], timeout: Optional[int] = None, 
                   input_data: Optional[str] = None, cwd: Optional[str] = None) -> Dict[str, Any]:
        """
        Run command safely with validation and limits.
        
        Args:
            cmd: Command and arguments list
            timeout: Timeout in seconds
            input_data: Optional input data
            cwd: Working directory
            
        Returns:
            Dict with returncode, stdout, stderr, success
            
        Raises:
            ValueError: If command is invalid
            subprocess.TimeoutExpired: If command times out
        """
        if not isinstance(cmd, list) or len(cmd) == 0:
            raise ValueError("Command must be a non-empty list")
        
        # Validate binary exists and is executable
        binary_path = cmd[0]
        if not os.path.exists(binary_path):
            raise ValueError(f"Binary not found: {binary_path}")
        
        if not os.access(binary_path, os.X_OK):
            raise ValueError(f"Binary not executable: {binary_path}")
        
        # Validate arguments
        for arg in cmd[1:]:
            if not isinstance(arg, str):
                raise ValueError("All command arguments must be strings")
            
            # Check for command injection patterns
            for pattern in SecurityValidator.DANGEROUS_PATTERNS:
                if re.search(pattern, arg):
                    raise ValueError(f"Argument contains dangerous pattern: {arg}")
        
        timeout = timeout or cls.DEFAULT_TIMEOUT
        
        try:
            logger.info(f"Running command: {' '.join(cmd[:2])} [args hidden]")
            
            result = subprocess.run(
                cmd,
                input=input_data,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
                # Security: Don't inherit environment, use minimal environment
                env={'PATH': '/usr/local/bin:/usr/bin:/bin'}
            )
            
            # Check output size
            output_size = len(result.stdout) + len(result.stderr)
            if output_size > cls.MAX_OUTPUT_SIZE:
                raise ValueError(f"Command output too large: {output_size} bytes")
            
            return {
                'success': result.returncode == 0,
                'returncode': result.returncode,
                'stdout': result.stdout,
                'stderr': result.stderr,
                'timeout': False
            }
            
        except subprocess.TimeoutExpired as e:
            logger.error(f"Command timed out after {timeout}s: {cmd[0]}")
            return {
                'success': False,
                'returncode': -1,
                'stdout': '',
                'stderr': f'Command timed out after {timeout}s',
                'timeout': True
            }
        except Exception as e:
            logger.error(f"Command failed: {e}")
            return {
                'success': False,
                'returncode': -1,
                'stdout': '',
                'stderr': str(e),
                'timeout': False
            }


class SecureTempFile:
    """Secure temporary file operations."""
    
    @classmethod
    def create_secure_temp_file(cls, content: str, suffix: str = '.js', 
                               prefix: str = 'jsextractor_') -> 'SecureTempFile':
        """
        Create a secure temporary file.
        
        Args:
            content: Content to write
            suffix: File suffix
            prefix: File prefix
            
        Returns:
            SecureTempFile instance
        """
        # Validate inputs
        SecurityValidator.validate_js_content(content)
        
        if not suffix.startswith('.'):
            suffix = '.' + suffix
        
        # Create secure temporary directory
        temp_dir = tempfile.mkdtemp(prefix=prefix + 'dir_')
        
        # Create file with secure permissions
        fd, path = tempfile.mkstemp(suffix=suffix, prefix=prefix, dir=temp_dir)
        
        try:
            # Set secure permissions (owner read/write only)
            os.chmod(path, 0o600)
            
            # Write content
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())  # Ensure data is written
            
            return cls(path, temp_dir)
            
        except Exception:
            # Cleanup on error
            try:
                os.unlink(path)
            except:
                pass
            try:
                os.rmdir(temp_dir)
            except:
                pass
            raise
    
    def __init__(self, file_path: str, temp_dir: str):
        self.file_path = file_path
        self.temp_dir = temp_dir
        self._closed = False
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
    
    def cleanup(self):
        """Securely cleanup temporary files."""
        if self._closed:
            return
        
        try:
            if os.path.exists(self.file_path):
                # Secure deletion - overwrite before delete
                with open(self.file_path, 'r+b') as f:
                    length = f.seek(0, 2)  # Get file length
                    f.seek(0)
                    f.write(secrets.token_bytes(length))  # Overwrite with random data
                    f.flush()
                    os.fsync(f.fileno())
                
                os.unlink(self.file_path)
            
            if os.path.exists(self.temp_dir):
                os.rmdir(self.temp_dir)
                
        except Exception as e:
            logger.error(f"Failed to cleanup temp file: {e}")
        
        self._closed = True
    
    def __del__(self):
        self.cleanup()


class SecurityConfig:
    """Security configuration and settings."""
    
    # Rate limiting (requests per minute)
    RATE_LIMIT_PER_MINUTE = 100
    
    # Maximum concurrent processing
    MAX_CONCURRENT_ANALYSES = 10
    
    # Allowed origins for CORS (should be configured)
    ALLOWED_ORIGINS = ['http://localhost:3000']  # Development only
    
    # Security headers
    SECURITY_HEADERS = {
        'X-Content-Type-Options': 'nosniff',
        'X-Frame-Options': 'DENY',
        'X-XSS-Protection': '1; mode=block',
        'Strict-Transport-Security': 'max-age=31536000; includeSubDomains',
        'Content-Security-Policy': "default-src 'self'",
    }
    
    # Sensitive data patterns for logging
    SENSITIVE_PATTERNS = [
        r'api[_-]?key',
        r'password',
        r'secret',
        r'token',
        r'bearer',
        r'sk_live_',
        r'pk_live_',
    ]
    
    @classmethod
    def sanitize_for_logging(cls, data: str) -> str:
        """Remove sensitive data from logs."""
        sanitized = data
        for pattern in cls.SENSITIVE_PATTERNS:
            sanitized = re.sub(f'{pattern}[=:][\'"]?[^\\s\'"]+', f'{pattern}=***REDACTED***', 
                             sanitized, flags=re.IGNORECASE)
        return sanitized