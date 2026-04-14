"""
Native Python implementation of sourcemap processing functionality.
Replaces the external sourcemapper binary dependency with pure Python implementation.
"""

import json
import base64
import re
import logging
from typing import Dict, List, Optional, Any
from urllib.parse import urljoin

from .http_fetcher import robust_fetcher
from .security_utils import SecurityValidator

logger = logging.getLogger(__name__)


class SourceMapDecodeError(Exception):
    """Exception raised when source map decoding fails"""
    pass


class NativeSourceMapProcessor:
    """
    Native Python implementation of source map processing.
    
    Features:
    - Parse source map JSON (version 3 format)
    - Decode embedded sources from base64
    - Resolve source file URLs
    - Reconstruct original source files
    - HTTP fetching with security validation
    """
    
    def __init__(
        self,
        timeout: int = 30,
        max_sourcemap_size_bytes: int = 50 * 1024 * 1024,
        max_reconstructed_files: int = 1000,
    ):
        self.timeout = max(1, int(timeout))
        self.max_sourcemap_size_bytes = max(1, int(max_sourcemap_size_bytes))
        self.max_reconstructed_files = max(1, int(max_reconstructed_files))
        self.headers = {
            "User-Agent": "JS-Security-Extractor/3.0-SourceMapProcessor",
            "Accept": "application/json, text/plain, */*"
        }
        self.last_fetch_metadata: Dict[str, Any] = {}
    
    async def process_sourcemap_from_url(self, js_url: str, sourcemap_url: str = None, 
                                       custom_headers: Dict[str, str] = None) -> Dict[str, Any]:
        """
        Process source map from JavaScript URL or direct sourcemap URL.
        
        Args:
            js_url: URL of the JavaScript file
            sourcemap_url: Direct URL to sourcemap (optional)
            custom_headers: Custom HTTP headers for requests
            
        Returns:
            Dictionary containing reconstructed files and metadata
        """
        self.last_fetch_metadata = {}
        try:
            # Determine the actual sourcemap URL
            target_url = sourcemap_url
            if not target_url:
                # Try to extract sourcemap URL from JavaScript file
                target_url = await self._extract_sourcemap_url_from_js_url(js_url, custom_headers)
                
            if not target_url:
                return {
                    'success': False,
                    'error': 'No source map URL found',
                    'files': [],
                    'stats': {'total_files': 0, 'total_size': 0}
                }
            
            # Validate and fetch source map
            SecurityValidator.validate_url(target_url)
            sourcemap_content = await self._fetch_sourcemap(target_url, custom_headers)
            
            # Process the source map content
            return await self.process_sourcemap_from_content(sourcemap_content, js_url)
            
        except Exception as e:
            logger.error(f"Source map URL processing failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'files': [],
                'stats': {'total_files': 0, 'total_size': 0}
            }
    
    async def process_sourcemap_from_content(
        self,
        sourcemap_content: str,
        base_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Process source map from in-memory content.
        
        Args:
            sourcemap_content: Source map JSON content
            base_url: Optional base URL from caller context (kept for call compatibility)
            
        Returns:
            Dictionary containing reconstructed files and metadata
        """
        try:
            # Parse source map JSON
            sourcemap_data = self._parse_sourcemap_json(sourcemap_content)
            
            # Extract source files
            reconstructed_files, extraction_meta = self._extract_source_files(sourcemap_data)
            
            # Calculate statistics
            stats = {
                'total_files': len(reconstructed_files),
                'total_size': sum(f['size'] for f in reconstructed_files),
                'js_files': len([f for f in reconstructed_files if f['type'] == 'javascript']),
                'other_files': len([f for f in reconstructed_files if f['type'] != 'javascript']),
                'sourcemap_version': sourcemap_data.get('version', 'unknown'),
                'truncated': extraction_meta["truncated"],
                'sources_with_content': extraction_meta["sources_with_content"],
                'max_reconstructed_files': self.max_reconstructed_files,
            }
            
            logger.info(f"Successfully reconstructed {stats['total_files']} files from source map")
            
            return {
                'success': True,
                'files': reconstructed_files,
                'stats': stats,
                'sourcemap_info': {
                    'version': sourcemap_data.get('version'),
                    'file': sourcemap_data.get('file'),
                    'sources_count': len(sourcemap_data.get('sources', [])),
                    'names_count': len(sourcemap_data.get('names', [])),
                    'has_sources_content': bool(sourcemap_data.get('sourcesContent'))
                }
            }
            
        except Exception as e:
            logger.error(f"Source map content processing failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'files': [],
                'stats': {'total_files': 0, 'total_size': 0}
            }
    
    def _parse_sourcemap_json(self, sourcemap_content: str) -> Dict[str, Any]:
        """Parse and validate source map JSON"""
        try:
            # Handle potential data URI
            if sourcemap_content.startswith('data:'):
                # Extract JSON from data URI
                if 'base64,' in sourcemap_content:
                    _, b64_data = sourcemap_content.split('base64,', 1)
                    sourcemap_content = base64.b64decode(b64_data).decode('utf-8')
                else:
                    # Handle plain text data URI
                    _, content = sourcemap_content.split(',', 1)
                    sourcemap_content = content
            
            sourcemap_data = json.loads(sourcemap_content)
            
            # Validate source map format
            if not isinstance(sourcemap_data, dict):
                raise SourceMapDecodeError("Source map must be a JSON object")
            
            version = sourcemap_data.get('version')
            if version != 3:
                logger.warning(f"Unsupported source map version: {version} (only version 3 fully supported)")
            
            required_fields = ['sources']
            for field in required_fields:
                if field not in sourcemap_data:
                    raise SourceMapDecodeError(f"Missing required field: {field}")
            
            return sourcemap_data
            
        except json.JSONDecodeError as e:
            raise SourceMapDecodeError(f"Invalid JSON in source map: {e}")
    
    def _extract_source_files(self, sourcemap_data: Dict[str, Any]) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Extract and reconstruct source files from source map data"""
        files = []
        truncated = False
        sources_with_content = 0
        
        sources = sourcemap_data.get('sources', [])
        sources_content = sourcemap_data.get('sourcesContent', [])
        
        # Ensure sources_content has same length as sources
        if sources_content and len(sources_content) != len(sources):
            logger.warning(f"sources ({len(sources)}) and sourcesContent ({len(sources_content)}) length mismatch")
            # Pad with None values if needed
            while len(sources_content) < len(sources):
                sources_content.append(None)
        
        for i, source_path in enumerate(sources):
            try:
                # Get source content
                source_content = None
                if sources_content and i < len(sources_content) and sources_content[i] is not None:
                    source_content = sources_content[i]
                else:
                    # No embedded content, would need to fetch from URL
                    logger.info(f"Source content not embedded for {source_path}, skipping")
                    continue

                sources_with_content += 1
                if len(files) >= self.max_reconstructed_files:
                    truncated = True
                    continue
                
                # Clean and normalize the source path
                normalized_path = self._normalize_source_path(source_path)
                
                # Determine file type
                file_type = self._determine_file_type(normalized_path)
                
                # Build file info
                file_info = {
                    'path': normalized_path,
                    'content': source_content,
                    'size': len(source_content) if source_content else 0,
                    'type': file_type,
                    'encoding': 'utf-8',
                    'original_path': source_path,
                    'source_index': i
                }
                
                files.append(file_info)
                
            except Exception as e:
                logger.warning(f"Failed to extract source file {i} ({source_path}): {e}")
                continue
        
        return files, {
            "truncated": truncated,
            "sources_with_content": sources_with_content,
        }
    
    def _normalize_source_path(self, source_path: str) -> str:
        """Normalize source file path for safe storage"""
        if not source_path:
            return f"unknown_{len(source_path)}.js"
        
        # Remove webpack:// and similar prefixes
        normalized = re.sub(r'^webpack://[^/]*/', '', source_path)
        normalized = re.sub(r'^webpack:///', '', normalized)
        
        # Remove leading slashes and dots
        normalized = normalized.lstrip('./')
        
        # Replace backslashes with forward slashes
        normalized = normalized.replace('\\', '/')
        
        # Ensure we have a valid filename
        if not normalized or normalized == '/':
            normalized = "unknown.js"
        
        # Split path and validate each component
        path_parts = []
        for part in normalized.split('/'):
            if part and part not in ['..', '.']:
                # Sanitize each path component
                sanitized_part = re.sub(r'[^\w\-\.]', '_', part)
                if sanitized_part:
                    path_parts.append(sanitized_part)
        
        if not path_parts:
            return "unknown.js"
        
        return '/'.join(path_parts)
    
    def _determine_file_type(self, file_path: str) -> str:
        """Determine file type from path"""
        if not file_path:
            return 'unknown'
        
        extension = file_path.split('.')[-1].lower() if '.' in file_path else ''
        
        type_mapping = {
            'js': 'javascript',
            'mjs': 'javascript',
            'jsx': 'javascript-react',
            'ts': 'typescript',
            'tsx': 'typescript-react',
            'css': 'stylesheet',
            'scss': 'sass',
            'sass': 'sass',
            'less': 'less',
            'html': 'html',
            'htm': 'html',
            'json': 'json',
            'xml': 'xml',
            'vue': 'vue',
            'py': 'python',
            'java': 'java',
            'c': 'c',
            'cpp': 'cpp',
            'cc': 'cpp',
            'cs': 'csharp',
            'php': 'php'
        }
        
        return type_mapping.get(extension, 'text')
    
    async def _extract_sourcemap_url_from_js_url(self, js_url: str, custom_headers: Dict[str, str] = None) -> Optional[str]:
        """Extract sourcemap URL from JavaScript file content"""
        try:
            # Fetch JavaScript content using robust fetcher
            headers = dict(self.headers)
            if custom_headers:
                headers.update(custom_headers)
            
            fetcher = robust_fetcher.__class__(
                timeout_seconds=self.timeout if isinstance(self.timeout, (int, float)) else 30,
            )
            
            result = await fetcher.fetch_text(js_url, headers=headers, check_content_type=False)
            
            if not result.success:
                logger.error(f"Failed to fetch JavaScript from {js_url}: {result.error_message}")
                return None
            
            # Extract sourcemap URL from content
            return self._extract_sourcemap_url_from_content(result.content, js_url)
            
        except Exception as e:
            logger.error(f"Failed to fetch JavaScript from {js_url}: {e}")
            return None
    
    def _extract_sourcemap_url_from_content(self, js_content: str, js_url: str) -> Optional[str]:
        """Extract sourcemap URL from JavaScript content comments"""
        # Look for sourcemap comments (most common patterns)
        patterns = [
            r'//# sourceMappingURL=(.+?)(?:\n|$)',
            r'/\*# sourceMappingURL=(.+?)\*/',
            r'//@ sourceMappingURL=(.+?)(?:\n|$)',  # Older format
        ]
        
        for pattern in patterns:
            match = re.search(pattern, js_content)
            if match:
                sourcemap_url = match.group(1).strip()
                
                # Handle data URLs (embedded sourcemaps)
                if sourcemap_url.startswith('data:'):
                    return sourcemap_url
                
                # Resolve relative URLs
                if not sourcemap_url.startswith(('http://', 'https://')):
                    base_url = js_url.rsplit('/', 1)[0]
                    sourcemap_url = urljoin(base_url + '/', sourcemap_url)
                
                return sourcemap_url
        
        # Fallback: try conventional .map file
        if js_url.endswith('.js'):
            conventional_url = js_url + '.map'
            logger.info(f"No sourcemap comment found, trying conventional URL: {conventional_url}")
            return conventional_url
        
        return None
    
    async def _fetch_sourcemap(self, url: str, custom_headers: Dict[str, str] = None) -> str:
        """Fetch source map content from URL with security validation"""
        headers = dict(self.headers)
        if custom_headers:
            headers.update(custom_headers)
        
        # Create fetcher with sourcemap-specific settings
        fetcher = robust_fetcher.__class__(
            timeout_seconds=self.timeout if isinstance(self.timeout, (int, float)) else 30,
            max_response_size=self.max_sourcemap_size_bytes,
        )
        
        result = await fetcher.fetch_text(url, headers=headers, check_content_type=False)
        self.last_fetch_metadata = {
            "url": url,
            "fetched": bool(result.success),
            "http_status": result.status_code,
            "content_type": result.content_type,
            "error_type": result.error_type,
            "error_message": result.error_message,
        }
        
        if not result.success:
            if result.error_type == "response_too_large":
                raise SourceMapDecodeError(f"Source map too large: {result.error_message}")
            elif result.status_code:
                raise SourceMapDecodeError(f"HTTP error fetching source map: {result.status_code}")
            else:
                raise SourceMapDecodeError(f"Request error fetching source map: {result.error_message}")
        
        return result.content


# For backward compatibility, create an alias
SourceMapProcessor = NativeSourceMapProcessor
