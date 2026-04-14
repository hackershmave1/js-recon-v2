"""
Secure JavaScript analysis using jsluice tool with comprehensive input validation and security controls.
"""
import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from .security_utils import (
    SecurityValidator, 
    SecureSubprocess, 
    SecureTempFile,
    SecurityConfig
)

logger = logging.getLogger(__name__)

class SecureJSluiceExtractor:
    """
    Secure JavaScript analysis using jsluice tool.
    Includes comprehensive input validation and security controls.
    """
    
    def __init__(self, jsluice_binary: str = "/usr/local/bin/jsluice"):
        self.jsluice_binary = jsluice_binary
        self._validate_binary()
        self._stats = {
            'urls_extracted': 0,
            'secrets_extracted': 0,
            'errors_encountered': 0,
            'last_extraction': None
        }
    
    def _validate_binary(self):
        """Validate jsluice binary exists and is executable with security checks."""
        # Use security validator
        if not self.jsluice_binary.startswith('/usr/local/bin/') and not self.jsluice_binary.startswith('/usr/bin/'):
            raise ValueError(f"jsluice binary must be in approved directory: {self.jsluice_binary}")
        
        # Check with SecureSubprocess
        try:
            result = SecureSubprocess.run_command([self.jsluice_binary, '--help'], timeout=5)
            if not result['success']:
                raise RuntimeError(f"jsluice binary check failed: {result['stderr']}")
        except Exception as e:
            raise RuntimeError(f"jsluice binary validation failed: {e}")
        
        logger.info(f"jsluice binary validated: {self.jsluice_binary}")
    
    def extract_urls(self, js_content: str, base_url: str = None, 
                    resolve_urls: bool = True, options: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        Extract URLs using jsluice with comprehensive security validation.
        
        Args:
            js_content: JavaScript source code
            base_url: Base URL for resolving relative paths
            resolve_urls: Whether to resolve relative URLs
            options: Additional options for extraction
            
        Returns:
            List of URL objects with metadata
            
        Raises:
            ValueError: If inputs are invalid or dangerous
            RuntimeError: If extraction fails
        """
        start_time = datetime.utcnow()
        options = options or {}
        
        try:
            # Security validation
            validated_content = SecurityValidator.validate_js_content(js_content)
            
            if base_url:
                validated_base_url = SecurityValidator.validate_url(base_url)
            else:
                validated_base_url = None
            
            # Create secure temporary file
            with SecureTempFile.create_secure_temp_file(validated_content, '.js') as temp_file:
                
                # Build command securely
                cmd = [self.jsluice_binary, "urls"]
                
                # Add options safely
                if resolve_urls and validated_base_url:
                    cmd.extend(["--resolve", validated_base_url])
                elif resolve_urls:
                    cmd.append("--resolve")
                
                # Add source inclusion if requested
                if options.get('include_source', True):
                    cmd.append("--include-source")
                
                # Add unique flag if requested
                if options.get('unique', True):
                    cmd.append("--unique")
                
                # Add file path
                cmd.append(temp_file.file_path)
                
                # Run command securely
                result = SecureSubprocess.run_command(
                    cmd,
                    timeout=options.get('timeout', 30)
                )
                
                if not result['success']:
                    self._stats['errors_encountered'] += 1
                    error_msg = SecurityConfig.sanitize_for_logging(result['stderr'])
                    logger.error(f"jsluice URL extraction failed: {error_msg}")
                    raise RuntimeError(f"jsluice URL extraction failed: {error_msg}")
                
                # Parse output securely
                urls = self._parse_jsluice_output(result['stdout'], 'url')
                
                # Update stats
                self._stats['urls_extracted'] += len(urls)
                self._stats['last_extraction'] = datetime.utcnow().isoformat()
                
                processing_time_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
                
                logger.info(f"jsluice extracted {len(urls)} URLs in {processing_time_ms}ms")
                
                return urls
                
        except ValueError as e:
            logger.error(f"jsluice URL extraction validation failed: {e}")
            self._stats['errors_encountered'] += 1
            raise
        except Exception as e:
            logger.error(f"jsluice URL extraction failed: {e}")
            self._stats['errors_encountered'] += 1
            raise RuntimeError(f"URL extraction failed: {str(e)}")
    
    def extract_secrets(self, js_content: str, custom_patterns_file: str = None, 
                       options: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        Extract secrets using jsluice with security validation.
        
        Args:
            js_content: JavaScript source code
            custom_patterns_file: Path to custom patterns file
            options: Additional options for extraction
            
        Returns:
            List of secret objects with metadata
            
        Raises:
            ValueError: If inputs are invalid or dangerous
            RuntimeError: If extraction fails
        """
        start_time = datetime.utcnow()
        options = options or {}
        
        try:
            # Security validation
            validated_content = SecurityValidator.validate_js_content(js_content)
            
            if custom_patterns_file:
                validated_patterns_file = SecurityValidator.validate_file_path(custom_patterns_file)
                if not os.path.exists(validated_patterns_file):
                    raise ValueError(f"Patterns file not found: {custom_patterns_file}")
            else:
                validated_patterns_file = None
            
            # Create secure temporary file
            with SecureTempFile.create_secure_temp_file(validated_content, '.js') as temp_file:
                
                # Build command securely
                cmd = [self.jsluice_binary, "secrets"]
                
                # Add patterns file if provided
                if validated_patterns_file:
                    cmd.extend(["--patterns", validated_patterns_file])
                
                # Add source inclusion if requested
                if options.get('include_source', True):
                    cmd.append("--include-source")
                
                # Add file path
                cmd.append(temp_file.file_path)
                
                # Run command securely
                result = SecureSubprocess.run_command(
                    cmd,
                    timeout=options.get('timeout', 30)
                )
                
                if not result['success']:
                    self._stats['errors_encountered'] += 1
                    error_msg = SecurityConfig.sanitize_for_logging(result['stderr'])
                    logger.error(f"jsluice secret extraction failed: {error_msg}")
                    raise RuntimeError(f"jsluice secret extraction failed: {error_msg}")
                
                # Parse output securely
                secrets = self._parse_jsluice_output(result['stdout'], 'secret')
                
                # Filter out potential false positives and sanitize
                filtered_secrets = self._filter_and_sanitize_secrets(secrets)
                
                # Update stats
                self._stats['secrets_extracted'] += len(filtered_secrets)
                self._stats['last_extraction'] = datetime.utcnow().isoformat()
                
                processing_time_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
                
                logger.info(f"jsluice extracted {len(filtered_secrets)} secrets in {processing_time_ms}ms")
                
                return filtered_secrets
                
        except ValueError as e:
            logger.error(f"jsluice secret extraction validation failed: {e}")
            self._stats['errors_encountered'] += 1
            raise
        except Exception as e:
            logger.error(f"jsluice secret extraction failed: {e}")
            self._stats['errors_encountered'] += 1
            raise RuntimeError(f"Secret extraction failed: {str(e)}")
    
    def extract_comprehensive(self, js_content: str, base_url: str = None, 
                            options: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Perform comprehensive analysis combining URLs and secrets with security controls.
        
        Args:
            js_content: JavaScript source code
            base_url: Base URL for resolving relative paths
            options: Additional options for extraction
            
        Returns:
            Combined analysis results with metadata
        """
        start_time = datetime.utcnow()
        options = options or {}
        
        try:
            # Extract URLs and secrets
            urls = self.extract_urls(js_content, base_url, 
                                   options.get('resolve_urls', True), options)
            secrets = self.extract_secrets(js_content, 
                                         options.get('patterns_file'), options)
            
            processing_time_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
            
            return {
                'success': True,
                'analysis': {
                    'urls': urls,
                    'secrets': secrets
                },
                'stats': {
                    'urls_found': len(urls),
                    'secrets_found': len(secrets),
                    'processing_time_ms': processing_time_ms,
                    'content_size_bytes': len(js_content)
                },
                'metadata': {
                    'extractor': 'secure_jsluice',
                    'version': '1.0.0',
                    'timestamp': start_time.isoformat(),
                    'base_url': base_url,
                    'options': options
                },
                'extractor_stats': self._stats.copy()
            }
            
        except Exception as e:
            logger.error(f"Comprehensive jsluice analysis failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'stats': {
                    'urls_found': 0,
                    'secrets_found': 0,
                    'processing_time_ms': int((datetime.utcnow() - start_time).total_seconds() * 1000)
                },
                'extractor_stats': self._stats.copy()
            }
    
    def _parse_jsluice_output(self, output: str, output_type: str) -> List[Dict[str, Any]]:
        """
        Safely parse jsluice JSONL output.
        
        Args:
            output: Raw jsluice output
            output_type: Type of output ('url' or 'secret')
            
        Returns:
            List of parsed objects
        """
        results = []
        
        for line_num, line in enumerate(output.strip().split('\n'), 1):
            if not line.strip():
                continue
            
            try:
                data = json.loads(line)
                
                if output_type == 'url':
                    parsed = self._parse_url_result(data, line_num)
                elif output_type == 'secret':
                    parsed = self._parse_secret_result(data, line_num)
                else:
                    logger.warning(f"Unknown output type: {output_type}")
                    continue
                
                if parsed:
                    results.append(parsed)
                
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse jsluice output line {line_num}: {e}")
                continue
            except Exception as e:
                logger.warning(f"Error processing jsluice output line {line_num}: {e}")
                continue
        
        return results
    
    def _parse_url_result(self, data: Dict[str, Any], line_num: int) -> Optional[Dict[str, Any]]:
        """Parse URL result with validation."""
        try:
            url = data.get('url', '')
            if not url:
                return None
            
            # Validate extracted URL
            try:
                validated_url = SecurityValidator.validate_url(url)
            except ValueError:
                logger.debug(f"Skipping invalid URL from jsluice: {url}")
                return None
            
            return {
                'url': validated_url,
                'source': data.get('source', ''),
                'line': data.get('line', 0),
                'column': data.get('column', 0),
                'context': data.get('context', '')[:200],  # Limit context size
                'type': 'jsluice_url',
                'confidence': 'high',
                'extractor': 'jsluice',
                'metadata': {
                    'line_num': line_num,
                    'raw_data': data
                }
            }
            
        except Exception as e:
            logger.warning(f"Failed to parse URL result: {e}")
            return None
    
    def _parse_secret_result(self, data: Dict[str, Any], line_num: int) -> Optional[Dict[str, Any]]:
        """Parse secret result with validation and sanitization."""
        try:
            value = data.get('match', '')
            if not value or len(value) < 4:  # Skip very short matches
                return None
            
            rule = data.get('rule', 'unknown')
            confidence = self._map_jsluice_confidence(data.get('confidence', 'low'))
            
            return {
                'value': value[:100],  # Limit value size for security
                'type': rule,
                'source': data.get('source', '')[:50],  # Limit source size
                'line': data.get('line', 0),
                'column': data.get('column', 0),
                'context': data.get('context', '')[:100],  # Limit context size
                'confidence': confidence,
                'extractor': 'jsluice',
                'metadata': {
                    'line_num': line_num,
                    'rule': rule,
                    'original_confidence': data.get('confidence')
                }
            }
            
        except Exception as e:
            logger.warning(f"Failed to parse secret result: {e}")
            return None
    
    def _filter_and_sanitize_secrets(self, secrets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Filter out false positives and sanitize secret data.
        
        Args:
            secrets: Raw secrets from jsluice
            
        Returns:
            Filtered and sanitized secrets
        """
        filtered = []
        
        # Common false positive patterns
        false_positive_patterns = [
            r'^test',
            r'^example',
            r'^sample',
            r'^demo',
            r'^placeholder',
            r'^xxx+',
            r'^aaa+',
            r'^123+',
            r'^\*+$',
        ]
        
        for secret in secrets:
            value = secret.get('value', '').lower()
            
            # Skip if matches false positive patterns
            if any(re.match(pattern, value, re.IGNORECASE) for pattern in false_positive_patterns):
                logger.debug(f"Filtering false positive secret: {value[:20]}...")
                continue
            
            # Skip if too short or too long
            if len(value) < 8 or len(value) > 200:
                continue
            
            # Add security classification
            secret['security_classification'] = self._classify_secret(secret)
            
            filtered.append(secret)
        
        return filtered
    
    def _classify_secret(self, secret: Dict[str, Any]) -> str:
        """
        Classify secret by security risk level.
        
        Args:
            secret: Secret object
            
        Returns:
            Risk classification ('critical', 'high', 'medium', 'low')
        """
        secret_type = secret.get('type', '').lower()
        value = secret.get('value', '').lower()
        
        # Critical: Production API keys, private keys
        if any(pattern in secret_type for pattern in ['private_key', 'secret_key', 'rsa']):
            return 'critical'
        
        if any(pattern in value for pattern in ['sk_live_', 'pk_live_', 'private']):
            return 'critical'
        
        # High: API keys, tokens
        if any(pattern in secret_type for pattern in ['api_key', 'token', 'bearer']):
            return 'high'
        
        # Medium: Passwords, secrets
        if any(pattern in secret_type for pattern in ['password', 'secret']):
            return 'medium'
        
        # Low: Other patterns
        return 'low'
    
    def _map_jsluice_confidence(self, jsluice_confidence: str) -> str:
        """Map jsluice confidence levels to our standard levels."""
        confidence_mapping = {
            'high': 'high',
            'medium': 'medium',
            'low': 'low',
            'info': 'low'
        }
        return confidence_mapping.get(jsluice_confidence.lower(), 'low')
    
    def get_stats(self) -> Dict[str, Any]:
        """Get extractor statistics."""
        return {
            **self._stats,
            'binary_path': self.jsluice_binary,
            'security_validated': True
        }
    
    def reset_stats(self):
        """Reset extractor statistics."""
        self._stats = {
            'urls_extracted': 0,
            'secrets_extracted': 0,
            'errors_encountered': 0,
            'last_extraction': None
        }