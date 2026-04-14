import subprocess
import tempfile
import os
import json
import shutil
import signal
import time
from typing import Dict, List, Optional, Any
import logging
from urllib.parse import urljoin, urlparse

from ..config import settings

logger = logging.getLogger(__name__)

class SourceMapProcessor:
    """
    Source map processing using sourcemapper Go tool for reconstructing original files.
    Integrates with the denandz/sourcemapper functionality.
    """
    
    def __init__(self, sourcemapper_binary: str = "/usr/local/bin/sourcemapper"):
        self.sourcemapper_binary = sourcemapper_binary
        self._validate_binary()
    
    def _validate_binary(self):
        """Validate sourcemapper binary exists and is executable"""
        if not os.path.exists(self.sourcemapper_binary):
            raise FileNotFoundError(f"sourcemapper binary not found at {self.sourcemapper_binary}")
        
        if not os.access(self.sourcemapper_binary, os.X_OK):
            raise PermissionError(f"sourcemapper binary not executable at {self.sourcemapper_binary}")
    
    def _validate_sourcemap_size(self, content: str) -> Dict[str, Any]:
        """Validate sourcemap content size against limits"""
        size_bytes = len(content.encode('utf-8'))
        if size_bytes > settings.sourcemap_max_size_bytes:
            return {
                'success': False,
                'error': f'Sourcemap too large: {size_bytes} bytes exceeds limit of {settings.sourcemap_max_size_bytes} bytes',
                'files': [],
                'stats': {'total_files': 0, 'total_size': 0}
            }
        return {'success': True}
    
    def _validate_reconstructed_files_count(self, files: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Validate number of reconstructed files against limits"""
        if len(files) > settings.sourcemap_max_reconstructed_files:
            limited_files = files[:settings.sourcemap_max_reconstructed_files]
            logger.warning(f"Truncated reconstructed files from {len(files)} to {settings.sourcemap_max_reconstructed_files}")
            
            return {
                'success': True,
                'limited': True,
                'files': limited_files,
                'truncated_count': len(files) - len(limited_files),
                'original_count': len(files)
            }
        return {'success': True, 'limited': False}
    
    def process_sourcemap_from_url(self, js_url: str, sourcemap_url: str = None, 
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
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                output_dir = os.path.join(temp_dir, "reconstructed")
                os.makedirs(output_dir, exist_ok=True)
                
                # Build command
                cmd = [self.sourcemapper_binary, "-output", output_dir]
                
                # Add custom headers if provided
                if custom_headers:
                    for key, value in custom_headers.items():
                        cmd.extend(["-header", f"{key}: {value}"])
                
                # Use direct sourcemap URL if provided, otherwise extract from JS
                target_url = sourcemap_url if sourcemap_url else js_url
                cmd.extend(["-url", target_url])
                
                # Add insecure flag for development/testing
                cmd.append("-insecure")
                
                logger.info(f"Running sourcemapper: {' '.join(cmd)}")
                
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=settings.sourcemap_processing_timeout_seconds)
                
                if result.returncode != 0:
                    logger.error(f"sourcemapper failed: {result.stderr}")
                    return {
                        'success': False,
                        'error': result.stderr,
                        'files': [],
                        'stats': {'total_files': 0, 'total_size': 0}
                    }
                
                # Collect reconstructed files
                reconstructed_files = self._collect_files(output_dir)
                
                # Validate file count limits
                file_validation = self._validate_reconstructed_files_count(reconstructed_files)
                limited = file_validation['limited']
                if limited:
                    reconstructed_files = file_validation['files']
                    logger.warning(f"Limited reconstruction from {file_validation['original_count']} to {len(reconstructed_files)} files")
                
                stats = {
                    'total_files': len(reconstructed_files),
                    'total_size': sum(f['size'] for f in reconstructed_files),
                    'js_files': len([f for f in reconstructed_files if f['path'].endswith('.js')]),
                    'other_files': len([f for f in reconstructed_files if not f['path'].endswith('.js')])
                }
                
                logger.info(f"sourcemapper reconstructed {stats['total_files']} files ({stats['total_size']} bytes)")
                
                result_data = {
                    'success': True,
                    'files': reconstructed_files,
                    'stats': stats,
                    'output': result.stdout,
                    'js_url': js_url,
                    'sourcemap_url': sourcemap_url
                }
                
                if limited:
                    result_data['limited'] = True
                    result_data['truncated_count'] = file_validation['truncated_count']
                
                return result_data
                
        except subprocess.TimeoutExpired:
            logger.error("sourcemapper processing timed out")
            return {
                'success': False,
                'error': 'Processing timed out',
                'files': [],
                'stats': {'total_files': 0, 'total_size': 0}
            }
        except Exception as e:
            logger.error(f"sourcemapper processing failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'files': [],
                'stats': {'total_files': 0, 'total_size': 0}
            }
    
    def process_sourcemap_from_content(self, sourcemap_content: str, base_url: str = None) -> Dict[str, Any]:
        """
        Process source map from in-memory content.
        
        Args:
            sourcemap_content: Source map JSON content
            base_url: Base URL for resolving relative paths
            
        Returns:
            Dictionary containing reconstructed files and metadata
        """
        try:
            # Validate sourcemap size first
            size_validation = self._validate_sourcemap_size(sourcemap_content)
            if not size_validation['success']:
                return size_validation
            with tempfile.TemporaryDirectory() as temp_dir:
                # Write sourcemap to temp file
                sourcemap_file = os.path.join(temp_dir, "sourcemap.js.map")
                with open(sourcemap_file, 'w') as f:
                    f.write(sourcemap_content)
                
                output_dir = os.path.join(temp_dir, "reconstructed")
                os.makedirs(output_dir, exist_ok=True)
                
                # Build command for local file
                cmd = [self.sourcemapper_binary, "-output", output_dir, "-file", sourcemap_file]
                
                if base_url:
                    cmd.extend(["-baseurl", base_url])
                
                logger.info(f"Running sourcemapper on content: {' '.join(cmd)}")
                
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=settings.sourcemap_processing_timeout_seconds)
                
                if result.returncode != 0:
                    logger.error(f"sourcemapper failed on content: {result.stderr}")
                    return {
                        'success': False,
                        'error': result.stderr,
                        'files': [],
                        'stats': {'total_files': 0, 'total_size': 0}
                    }
                
                # Collect reconstructed files
                reconstructed_files = self._collect_files(output_dir)
                
                # Validate file count limits
                file_validation = self._validate_reconstructed_files_count(reconstructed_files)
                limited = file_validation['limited']
                if limited:
                    reconstructed_files = file_validation['files']
                    logger.warning(f"Limited reconstruction from {file_validation['original_count']} to {len(reconstructed_files)} files")
                
                stats = {
                    'total_files': len(reconstructed_files),
                    'total_size': sum(f['size'] for f in reconstructed_files),
                    'js_files': len([f for f in reconstructed_files if f['path'].endswith('.js')]),
                    'other_files': len([f for f in reconstructed_files if not f['path'].endswith('.js')])
                }
                
                result_data = {
                    'success': True,
                    'files': reconstructed_files,
                    'stats': stats,
                    'output': result.stdout,
                    'method': 'content'
                }
                
                if limited:
                    result_data['limited'] = True
                    result_data['truncated_count'] = file_validation['truncated_count']
                
                return result_data
                
        except Exception as e:
            logger.error(f"sourcemapper content processing failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'files': [],
                'stats': {'total_files': 0, 'total_size': 0}
            }
    
    def _collect_files(self, output_dir: str) -> List[Dict[str, Any]]:
        """Collect all reconstructed files from output directory"""
        files = []
        
        for root, dirs, filenames in os.walk(output_dir):
            for filename in filenames:
                file_path = os.path.join(root, filename)
                relative_path = os.path.relpath(file_path, output_dir)
                
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    
                    file_info = {
                        'path': relative_path.replace('\\', '/'),  # Normalize path separators
                        'content': content,
                        'size': len(content),
                        'type': self._get_file_type(filename),
                        'encoding': 'utf-8'
                    }
                    
                    files.append(file_info)
                    
                except Exception as e:
                    logger.warning(f"Failed to read reconstructed file {file_path}: {e}")
                    continue
        
        return files
    
    def _get_file_type(self, filename: str) -> str:
        """Determine file type from filename"""
        extension = os.path.splitext(filename)[1].lower()
        type_mapping = {
            '.js': 'javascript',
            '.ts': 'typescript',
            '.jsx': 'javascript-react',
            '.tsx': 'typescript-react',
            '.css': 'stylesheet',
            '.html': 'html',
            '.json': 'json',
            '.map': 'sourcemap'
        }
        return type_mapping.get(extension, 'unknown')
    
    def extract_sourcemap_url_from_js(self, js_content: str, js_url: str) -> Optional[str]:
        """
        Extract sourcemap URL from JavaScript content comments.
        
        Args:
            js_content: JavaScript source code
            js_url: URL of the JavaScript file for resolving relative paths
            
        Returns:
            Absolute sourcemap URL or None
        """
        # Look for sourcemap comments
        patterns = [
            r'//# sourceMappingURL=(.+?)(?:\n|$)',
            r'/\*# sourceMappingURL=(.+?)\*/',
            r'//@ sourceMappingURL=(.+?)(?:\n|$)',  # Older format
        ]
        
        import re
        
        for pattern in patterns:
            match = re.search(pattern, js_content)
            if match:
                sourcemap_url = match.group(1).strip()
                
                # Handle data URLs
                if sourcemap_url.startswith('data:'):
                    return sourcemap_url
                
                # Resolve relative URLs
                if not sourcemap_url.startswith(('http://', 'https://')):
                    base_url = js_url.rsplit('/', 1)[0]
                    sourcemap_url = urljoin(base_url + '/', sourcemap_url)
                
                return sourcemap_url
        
        # Try fallback: assume .map file exists
        if js_url.endswith('.js'):
            return js_url + '.map'
        
        return None