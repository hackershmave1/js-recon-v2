import subprocess
import json
import tempfile
import os
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

class JSluiceExtractor:
    """
    JavaScript analysis using jsluice tool for comprehensive URL and secret extraction.
    More advanced than basic regex patterns.
    """
    
    def __init__(self, jsluice_binary: str = "/usr/local/bin/jsluice"):
        self.jsluice_binary = jsluice_binary
        self._validate_binary()
    
    def _validate_binary(self):
        """Validate jsluice binary exists and is executable"""
        if not os.path.exists(self.jsluice_binary):
            raise FileNotFoundError(f"jsluice binary not found at {self.jsluice_binary}")
        
        if not os.access(self.jsluice_binary, os.X_OK):
            raise PermissionError(f"jsluice binary not executable at {self.jsluice_binary}")
    
    def extract_urls(self, js_content: str, base_url: str = None, resolve_urls: bool = True) -> List[Dict[str, Any]]:
        """
        Extract URLs using jsluice - more comprehensive than basic regex.
        
        Args:
            js_content: JavaScript source code
            base_url: Base URL for resolving relative paths
            resolve_urls: Whether to resolve relative URLs
            
        Returns:
            List of URL objects with metadata
        """
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
                f.write(js_content)
                f.flush()
                
                cmd = [self.jsluice_binary, "urls", "--unique", "--include-source"]
                
                if resolve_urls and base_url:
                    cmd.extend(["--resolve-paths", base_url])
                
                cmd.append(f.name)
                
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                
                # Clean up temp file
                os.unlink(f.name)
                
                if result.returncode != 0:
                    logger.error(f"jsluice urls failed: {result.stderr}")
                    return []
                
                # Parse JSONL output
                urls = []
                for line in result.stdout.strip().split('\n'):
                    if line.strip():
                        try:
                            url_data = json.loads(line)
                            parsed_url = (
                                url_data.get('url')
                                or url_data.get('value')
                                or url_data.get('path')
                                or ''
                            )
                            if not parsed_url:
                                continue
                            parsed_line, parsed_column = self._extract_line_column(url_data)
                            urls.append({
                                'url': parsed_url,
                                'source': url_data.get('source', ''),
                                'line': parsed_line,
                                'column': parsed_column,
                                'context': url_data.get('source', '') or url_data.get('context', ''),
                                'type': 'jsluice_url',
                                'confidence': 'high',
                                'extractor': 'jsluice_urls',
                            })
                        except json.JSONDecodeError as e:
                            logger.warning(f"Failed to parse jsluice URL output: {e}")
                            continue
                
                logger.info(f"jsluice extracted {len(urls)} URLs")
                return urls
                
        except subprocess.TimeoutExpired:
            logger.error("jsluice URL extraction timed out")
            return []
        except Exception as e:
            logger.error(f"jsluice URL extraction failed: {e}")
            return []
    
    def extract_secrets(self, js_content: str, custom_patterns_file: str = None) -> List[Dict[str, Any]]:
        """
        Extract secrets using jsluice patterns.
        
        Args:
            js_content: JavaScript source code
            custom_patterns_file: Path to custom patterns file
            
        Returns:
            List of secret objects with metadata
        """
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
                f.write(js_content)
                f.flush()
                
                cmd = [self.jsluice_binary, "secrets"]
                
                if custom_patterns_file and os.path.exists(custom_patterns_file):
                    cmd.extend(["--patterns", custom_patterns_file])
                
                cmd.append(f.name)
                
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                
                # Clean up temp file
                os.unlink(f.name)
                
                if result.returncode != 0:
                    logger.error(f"jsluice secrets failed: {result.stderr}")
                    return []
                
                # Parse JSONL output
                secrets = []
                for line in result.stdout.strip().split('\n'):
                    if line.strip():
                        try:
                            secret_data = json.loads(line)
                            parsed_value = (
                                secret_data.get('match')
                                or secret_data.get('value')
                                or secret_data.get('secret')
                                or ''
                            )
                            if not parsed_value:
                                continue
                            parsed_line, parsed_column = self._extract_line_column(secret_data)
                            secrets.append({
                                'value': parsed_value,
                                'type': secret_data.get('rule', 'unknown'),
                                'source': secret_data.get('source', ''),
                                'line': parsed_line,
                                'column': parsed_column,
                                'context': secret_data.get('source', '') or secret_data.get('context', ''),
                                'confidence': self._map_jsluice_confidence(secret_data.get('confidence', 'low')),
                                'extractor': 'jsluice_secrets'
                            })
                        except json.JSONDecodeError as e:
                            logger.warning(f"Failed to parse jsluice secret output: {e}")
                            continue
                
                logger.info(f"jsluice extracted {len(secrets)} secrets")
                return secrets
                
        except subprocess.TimeoutExpired:
            logger.error("jsluice secret extraction timed out")
            return []
        except Exception as e:
            logger.error(f"jsluice secret extraction failed: {e}")
            return []
    
    def _map_jsluice_confidence(self, jsluice_confidence: str) -> str:
        """Map jsluice confidence levels to our standard levels"""
        confidence_mapping = {
            'high': 'high',
            'medium': 'medium', 
            'low': 'low',
            'info': 'low'
        }
        return confidence_mapping.get(jsluice_confidence.lower(), 'low')
    
    def extract_comprehensive(self, js_content: str, base_url: str = None) -> Dict[str, Any]:
        """
        Perform comprehensive analysis combining URLs and secrets.
        
        Returns:
            Combined analysis results
        """
        return {
            'urls': self.extract_urls(js_content, base_url),
            'secrets': self.extract_secrets(js_content),
            'extractor': 'jsluice',
            'timestamp': json.loads(json.dumps({}))  # Current timestamp placeholder
        }

    def _extract_line_column(self, payload: Dict[str, Any]) -> tuple[int | None, int | None]:
        line = payload.get('line')
        column = payload.get('column')
        location = payload.get('location')
        if isinstance(location, dict):
            start = location.get('start') if isinstance(location.get('start'), dict) else location
            line = line or start.get('line')
            column = column or start.get('column')
        return self._to_positive_int(line), self._to_positive_int(column)

    def _to_positive_int(self, value: Any) -> int | None:
        try:
            int_value = int(value)
            return int_value if int_value > 0 else None
        except Exception:
            return None
