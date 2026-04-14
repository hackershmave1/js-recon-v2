import logging
import re
from typing import Any, List
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class EndpointSanitizer:
    """
    Endpoint sanitization and noise filter pipeline inspired by xnLinkFinder.
    Removes malformed entries, noise patterns, and low-quality URLs while preserving
    high-signal findings.
    """
    
    # Patterns to remove unbalanced wrappers and malformed brackets
    MALFORMED_WRAPPER_PATTERNS = [
        re.compile(r'^[\(\[\{]+'),  # Leading brackets
        re.compile(r'[\)\]\}]+$'),  # Trailing brackets
        re.compile(r'^[\'"`]+'),    # Leading quotes
        re.compile(r'[\'"`]+$'),    # Trailing quotes
    ]
    
    # Known noisy domains and extensions to filter out
    NOISY_DOMAINS = {
        'localhost',
        '127.0.0.1',
        '0.0.0.0',
        'example.com',
        'test.com',
        'demo.com',
        'placeholder.com',
        'google-analytics.com',
        'googletagmanager.com',
        'doubleclick.net',
        'facebook.com',
        'twitter.com',
        'linkedin.com',
        'youtube.com',
        'vimeo.com',
    }
    
    # File extensions that are typically not API endpoints
    NOISY_EXTENSIONS = {
        '.js', '.css', '.html', '.htm', '.xml', '.json', '.txt', '.md',
        '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.webp', '.bmp',
        '.woff', '.woff2', '.ttf', '.eot', '.otf',
        '.mp4', '.mp3', '.wav', '.avi', '.mov', '.wmv',
        '.zip', '.tar', '.gz', '.rar', '.7z',
        '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    }
    
    # Path patterns that typically indicate build artifacts or static resources
    NOISY_PATH_PATTERNS = [
        re.compile(r'/(node_modules|bower_components)/'),
        re.compile(r'/(dist|build|static|public|assets)/'),
        re.compile(r'/__(tests?|mocks?|fixtures?)__/'),
        re.compile(r'\.min\.[a-z]+$'),
        re.compile(r'\.[a-f0-9]{8,}\.[a-z]+$'),  # Hashed filenames
        re.compile(r'/webpack/'),
        re.compile(r'/chunk\.[a-f0-9]+\.js$'),
        re.compile(r'/vendors\~[a-f0-9]+\.js$'),
    ]
    
    # Minimum length for valid endpoints
    MIN_ENDPOINT_LENGTH = 2
    MAX_ENDPOINT_LENGTH = 2000
    
    def __init__(self, enable_domain_filtering: bool = True, enable_extension_filtering: bool = True):
        """
        Initialize the sanitizer with optional filters.
        
        Args:
            enable_domain_filtering: Whether to filter known noisy domains
            enable_extension_filtering: Whether to filter file extensions
        """
        self.enable_domain_filtering = enable_domain_filtering
        self.enable_extension_filtering = enable_extension_filtering
    
    def sanitize_endpoints(self, endpoints: List[dict[str, Any]]) -> List[dict[str, Any]]:
        """
        Sanitize a list of endpoint results, removing noise and malformed entries.
        
        Args:
            endpoints: List of endpoint dictionaries with 'url' or 'endpoint' fields
            
        Returns:
            Filtered list of clean endpoints
        """
        if not endpoints:
            return endpoints
        
        sanitized = []
        original_count = len(endpoints)
        
        for endpoint_dict in endpoints:
            # Extract URL from various possible field names
            url = endpoint_dict.get('url') or endpoint_dict.get('endpoint', '')
            if not url:
                continue
            
            # Apply sanitization pipeline
            cleaned_url = self._clean_endpoint_url(url)
            if not cleaned_url:
                continue
                
            if not self._is_valid_endpoint(cleaned_url):
                continue
            
            # Update the cleaned URL back into the dict
            sanitized_endpoint = endpoint_dict.copy()
            if 'url' in sanitized_endpoint:
                sanitized_endpoint['url'] = cleaned_url
            if 'endpoint' in sanitized_endpoint:
                sanitized_endpoint['endpoint'] = cleaned_url
            
            sanitized.append(sanitized_endpoint)
        
        removed_count = original_count - len(sanitized)
        if removed_count > 0:
            logger.debug(f"Endpoint sanitization removed {removed_count}/{original_count} entries")
        
        return sanitized
    
    def _clean_endpoint_url(self, url: str) -> str | None:
        """
        Clean and normalize a single endpoint URL.
        
        Args:
            url: Raw URL string
            
        Returns:
            Cleaned URL string or None if invalid
        """
        if not url or not isinstance(url, str):
            return None
        
        # Remove leading/trailing whitespace
        cleaned = url.strip()
        
        # Filter non-printable characters but preserve normal URL chars
        cleaned = ''.join(char for char in cleaned if char.isprintable())
        
        # Remove malformed wrappers (unbalanced brackets, quotes)
        # But preserve URL parameter patterns like {id}, :param
        # First protect URL parameters
        param_placeholders = {}
        param_counter = 0
        
        # Protect {param} patterns
        def protect_brace_params(match):
            nonlocal param_counter
            placeholder = f"__PARAM_{param_counter}__"
            param_placeholders[placeholder] = match.group(0)
            param_counter += 1
            return placeholder
        
        cleaned = re.sub(r'\{[a-zA-Z0-9_-]+\}', protect_brace_params, cleaned)
        
        # Protect :param patterns  
        def protect_colon_params(match):
            nonlocal param_counter
            placeholder = f"__PARAM_{param_counter}__"
            param_placeholders[placeholder] = match.group(0)
            param_counter += 1
            return placeholder
        
        cleaned = re.sub(r':[a-zA-Z0-9_-]+', protect_colon_params, cleaned)
        
        # Now apply wrapper removal patterns
        for pattern in self.MALFORMED_WRAPPER_PATTERNS:
            cleaned = pattern.sub('', cleaned)
        
        # Restore URL parameters
        for placeholder, original in param_placeholders.items():
            cleaned = cleaned.replace(placeholder, original)
        
        # Remove duplicate slashes except after protocol
        if '://' in cleaned:
            protocol_part, rest = cleaned.split('://', 1)
            rest = re.sub(r'/+', '/', rest)
            cleaned = f"{protocol_part}://{rest}"
        else:
            cleaned = re.sub(r'/+', '/', cleaned)
        
        # Basic length check
        if len(cleaned) < self.MIN_ENDPOINT_LENGTH or len(cleaned) > self.MAX_ENDPOINT_LENGTH:
            return None
        
        return cleaned.strip()
    
    def _is_valid_endpoint(self, url: str) -> bool:
        """
        Determine if a cleaned URL represents a valid API endpoint.
        
        Args:
            url: Cleaned URL string
            
        Returns:
            True if URL appears to be a valid endpoint
        """
        if not url:
            return False
        
        # Filter whitespace-only or empty endpoints
        if not url.strip():
            return False
        
        # Must start with / or http(s)
        if not (url.startswith('/') or url.startswith('http')):
            return False
        
        # Check for noisy file extensions
        if self.enable_extension_filtering:
            lower_url = url.lower()
            for ext in self.NOISY_EXTENSIONS:
                if lower_url.endswith(ext):
                    return False
        
        # Check for noisy path patterns
        for pattern in self.NOISY_PATH_PATTERNS:
            if pattern.search(url):
                return False
        
        # Check for noisy domains (for absolute URLs)
        if self.enable_domain_filtering and url.startswith('http'):
            try:
                parsed = urlparse(url)
                # Check hostname without port
                hostname = parsed.hostname
                if hostname and hostname.lower() in self.NOISY_DOMAINS:
                    return False
            except Exception:
                # If URL parsing fails, it's probably malformed
                return False
        
        # Filter obviously malformed URLs
        if self._is_malformed_url(url):
            return False
        
        return True
    
    def _is_malformed_url(self, url: str) -> bool:
        """
        Check if URL appears to be malformed or contains obvious errors.
        
        Args:
            url: URL to check
            
        Returns:
            True if URL appears malformed
        """
        # Check for unbalanced brackets/quotes, but allow URL parameter patterns like {id}, :id
        # First check for URL parameter patterns and temporarily replace them
        url_for_checking = url
        url_for_checking = re.sub(r'\{[a-zA-Z0-9_-]+\}', 'PARAM', url_for_checking)  # {id} -> PARAM
        url_for_checking = re.sub(r':[a-zA-Z0-9_-]+', 'PARAM', url_for_checking)    # :id -> PARAM
        
        brackets = {'(': ')', '[': ']', '{': '}'}
        stack = []
        
        for char in url_for_checking:
            if char in brackets:
                stack.append(brackets[char])
            elif char in brackets.values():
                if not stack or stack.pop() != char:
                    # Unmatched closing bracket
                    return True
        
        # Remaining unclosed brackets
        if stack:
            return True
        
        # Check for invalid characters in URL path
        if re.search(r'[<>"\s]', url):
            return True
        
        # Check for JavaScript artifacts
        if any(pattern in url.lower() for pattern in [
            'undefined', 'null', 'nan', '[object object]', 'function()', '=>'
        ]):
            return True
        
        return False
    
    def get_filter_stats(self) -> dict[str, bool]:
        """
        Get current filter configuration.
        
        Returns:
            Dictionary of enabled filter settings
        """
        return {
            'domain_filtering_enabled': self.enable_domain_filtering,
            'extension_filtering_enabled': self.enable_extension_filtering,
            'noisy_domains_count': len(self.NOISY_DOMAINS),
            'noisy_extensions_count': len(self.NOISY_EXTENSIONS),
        }