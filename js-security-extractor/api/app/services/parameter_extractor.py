"""
Parameter Signal Extractor for B-023 - Mines parameter names from various sources
to identify input attack surface candidates.
"""

import json
import logging
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Set, Tuple
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)


class ParameterExtractor:
    """
    Extracts parameter names from JS variables, JSON keys, XML tags, HTML form fields,
    and URL query parameters with provenance tracking and confidence scoring.
    """

    def __init__(self):
        """Initialize parameter patterns for different source types."""
        self.js_patterns = {
            # JavaScript variable declarations
            "var_declaration": re.compile(r"\b(?:var|let|const)\s+([a-zA-Z_$][a-zA-Z0-9_$]*)\s*=", re.MULTILINE),
            
            # Object property access patterns
            "property_access": re.compile(r"\.([a-zA-Z_$][a-zA-Z0-9_$]*)", re.MULTILINE),
            
            # Object property definitions
            "object_property": re.compile(r"['\"]?([a-zA-Z_$][a-zA-Z0-9_$]*)['\"]?\s*:", re.MULTILINE),
            
            # Function parameters
            "function_params": re.compile(r"function\s+[a-zA-Z_$][a-zA-Z0-9_$]*\s*\(\s*([^)]+)\s*\)", re.MULTILINE),
            
            # Arrow function parameters
            "arrow_params": re.compile(r"\(\s*([^)]+)\s*\)\s*=>", re.MULTILINE),
            
            # Destructuring assignments
            "destructuring": re.compile(r"\{\s*([^}]+)\s*\}\s*=", re.MULTILINE),
            
            # Form field names in JavaScript
            "form_field": re.compile(r"(?:name|id)\s*[=:]\s*['\"]([a-zA-Z_][a-zA-Z0-9_-]*)['\"]", re.IGNORECASE),
        }
        
        self.html_patterns = {
            # Form input fields
            "input_name": re.compile(r"<input[^>]+name\s*=\s*['\"]([a-zA-Z_][a-zA-Z0-9_-]*)['\"]", re.IGNORECASE),
            "input_id": re.compile(r"<input[^>]+id\s*=\s*['\"]([a-zA-Z_][a-zA-Z0-9_-]*)['\"]", re.IGNORECASE),
            
            # Form elements
            "textarea_name": re.compile(r"<textarea[^>]+name\s*=\s*['\"]([a-zA-Z_][a-zA-Z0-9_-]*)['\"]", re.IGNORECASE),
            "select_name": re.compile(r"<select[^>]+name\s*=\s*['\"]([a-zA-Z_][a-zA-Z0-9_-]*)['\"]", re.IGNORECASE),
            
            # Data attributes
            "data_attr": re.compile(r"data-([a-zA-Z_][a-zA-Z0-9_-]*)", re.IGNORECASE),
        }
    
    def extract(self, content: str, source_file: str = None, content_type: str = None) -> List[Dict[str, Any]]:
        """
        Extract parameter names from content with provenance tracking.
        
        Args:
            content: File content to analyze
            source_file: Source file URL/path for provenance
            content_type: Content type hint for better parsing
            
        Returns:
            List of parameter records with metadata
        """
        if not content:
            if source_file and source_file.startswith(("http://", "https://")):
                return self._deduplicate_parameters(self._extract_url_parameters(source_file))
            return []
        
        parameters = []
        content_type = content_type or self._detect_content_type(content, source_file)
        
        if content_type in ["javascript", "js"]:
            parameters.extend(self._extract_js_parameters(content, source_file))
        elif content_type == "json":
            parameters.extend(self._extract_json_parameters(content, source_file))
        elif content_type == "xml":
            parameters.extend(self._extract_xml_parameters(content, source_file))
        elif content_type == "html":
            parameters.extend(self._extract_html_parameters(content, source_file))
        else:
            # Try all extractors for unknown content types
            parameters.extend(self._extract_js_parameters(content, source_file))
            parameters.extend(self._extract_json_parameters(content, source_file))
            parameters.extend(self._extract_html_parameters(content, source_file))
        
        # URL parameters if source_file is a URL
        if source_file and source_file.startswith(("http://", "https://")):
            parameters.extend(self._extract_url_parameters(source_file))
        
        # Deduplicate while preserving highest confidence entries
        return self._deduplicate_parameters(parameters)
    
    def _detect_content_type(self, content: str, source_file: str = None) -> str:
        """Detect content type from file extension or content analysis."""
        if source_file:
            if source_file.endswith(('.js', '.mjs', '.ts')):
                return "javascript"
            elif source_file.endswith('.json'):
                return "json"
            elif source_file.endswith(('.xml', '.svg')):
                return "xml"
            elif source_file.endswith(('.html', '.htm')):
                return "html"
        
        # Content-based detection
        content_lower = content.strip().lower()
        if content_lower.startswith('{') or content_lower.startswith('['):
            try:
                json.loads(content)
                return "json"
            except:
                pass
        
        if content_lower.startswith('<?xml') or content_lower.startswith('<'):
            return "xml" if '<?xml' in content_lower else "html"
        
        # Default to JavaScript for unknown types
        return "javascript"
    
    def _extract_js_parameters(self, content: str, source_file: str) -> List[Dict[str, Any]]:
        """Extract parameters from JavaScript content."""
        parameters = []
        lines = content.split('\n')
        
        for pattern_name, pattern in self.js_patterns.items():
            for line_num, line in enumerate(lines, 1):
                matches = pattern.finditer(line)
                for match in matches:
                    param_name = match.group(1)
                    if self._is_valid_parameter_name(param_name):
                        parameters.append({
                            "name": param_name,
                            "source": "javascript",
                            "pattern": pattern_name,
                            "line": line_num,
                            "context": line.strip(),
                            "file": source_file or "unknown",
                            "confidence": self._calculate_js_confidence(pattern_name, param_name, line)
                        })
        
        # Extract from function parameter lists
        parameters.extend(self._extract_function_parameters(content, source_file))
        
        return parameters
    
    def _extract_json_parameters(self, content: str, source_file: str) -> List[Dict[str, Any]]:
        """Extract parameter names from JSON content."""
        parameters = []
        
        try:
            # First try to parse as valid JSON
            data = json.loads(content)
            keys = self._extract_json_keys_recursive(data)
            
            for key in keys:
                if self._is_valid_parameter_name(key):
                    parameters.append({
                        "name": key,
                        "source": "json",
                        "pattern": "json_key",
                        "line": None,  # JSON doesn't preserve line info easily
                        "context": f"JSON key: {key}",
                        "file": source_file or "unknown",
                        "confidence": self._calculate_json_confidence(key)
                    })
        
        except json.JSONDecodeError:
            # Fallback to regex extraction for malformed JSON
            json_key_pattern = re.compile(r'["\']([a-zA-Z_][a-zA-Z0-9_-]*)["\']\s*:', re.MULTILINE)
            lines = content.split('\n')
            
            for line_num, line in enumerate(lines, 1):
                matches = json_key_pattern.finditer(line)
                for match in matches:
                    key = match.group(1)
                    if self._is_valid_parameter_name(key):
                        parameters.append({
                            "name": key,
                            "source": "json",
                            "pattern": "json_key_regex",
                            "line": line_num,
                            "context": line.strip(),
                            "file": source_file or "unknown",
                            "confidence": 0.7  # Lower confidence for regex extraction
                        })
        
        return parameters
    
    def _extract_xml_parameters(self, content: str, source_file: str) -> List[Dict[str, Any]]:
        """Extract parameter names from XML content."""
        parameters = []
        
        try:
            root = ET.fromstring(content)
            elements = self._extract_xml_elements_recursive(root)
            
            for element_name in elements:
                if self._is_valid_parameter_name(element_name):
                    parameters.append({
                        "name": element_name,
                        "source": "xml", 
                        "pattern": "xml_element",
                        "line": None,  # XML parsing doesn't preserve line info
                        "context": f"XML element: {element_name}",
                        "file": source_file or "unknown",
                        "confidence": self._calculate_xml_confidence(element_name)
                    })
        
        except ET.ParseError:
            # Fallback to regex extraction
            xml_element_pattern = re.compile(r'<([a-zA-Z_][a-zA-Z0-9_-]*)', re.MULTILINE)
            lines = content.split('\n')
            
            for line_num, line in enumerate(lines, 1):
                matches = xml_element_pattern.finditer(line)
                for match in matches:
                    element = match.group(1)
                    if self._is_valid_parameter_name(element):
                        parameters.append({
                            "name": element,
                            "source": "xml",
                            "pattern": "xml_element_regex",
                            "line": line_num,
                            "context": line.strip(),
                            "file": source_file or "unknown",
                            "confidence": 0.6  # Lower confidence for regex fallback
                        })
        
        return parameters
    
    def _extract_html_parameters(self, content: str, source_file: str) -> List[Dict[str, Any]]:
        """Extract parameter names from HTML content."""
        parameters = []
        lines = content.split('\n')
        
        for pattern_name, pattern in self.html_patterns.items():
            for line_num, line in enumerate(lines, 1):
                matches = pattern.finditer(line)
                for match in matches:
                    param_name = match.group(1)
                    if pattern_name == "data_attr" and "-" in param_name:
                        param_name = param_name.split("-", 1)[0]
                    if self._is_valid_parameter_name(param_name):
                        parameters.append({
                            "name": param_name,
                            "source": "html",
                            "pattern": pattern_name,
                            "line": line_num,
                            "context": line.strip(),
                            "file": source_file or "unknown",
                            "confidence": self._calculate_html_confidence(pattern_name, param_name)
                        })
        
        return parameters
    
    def _extract_url_parameters(self, url: str) -> List[Dict[str, Any]]:
        """Extract parameter names from URL query string."""
        parameters = []
        
        try:
            parsed = urlparse(url)
            query_params = parse_qs(parsed.query)
            
            for param_name in query_params.keys():
                if re.match(r"^[a-zA-Z_][a-zA-Z0-9_-]*$", param_name or ""):
                    parameters.append({
                        "name": param_name,
                        "source": "url_query",
                        "pattern": "query_parameter",
                        "line": None,
                        "context": f"URL query: {param_name}",
                        "file": url,
                        "confidence": 0.9  # High confidence for URL params
                    })
        
        except Exception as e:
            logger.debug(f"Failed to parse URL parameters from {url}: {e}")
        
        return parameters
    
    def _extract_function_parameters(self, content: str, source_file: str) -> List[Dict[str, Any]]:
        """Extract function parameter names from JavaScript."""
        parameters = []
        lines = content.split('\n')
        
        # Function parameters and destructuring
        param_patterns = [
            (re.compile(r"function\s+[a-zA-Z_$][a-zA-Z0-9_$]*\s*\(\s*([^)]+)\s*\)"), "function_params"),
            (re.compile(r"\(\s*([^)]+)\s*\)\s*=>"), "arrow_params"),
            (re.compile(r"\{\s*([^}]+)\s*\}\s*="), "destructuring")
        ]
        
        for line_num, line in enumerate(lines, 1):
            for pattern, pattern_name in param_patterns:
                matches = pattern.finditer(line)
                for match in matches:
                    param_string = match.group(1).strip()
                    param_names = self._parse_parameter_string(param_string)
                    
                    for param_name in param_names:
                        if self._is_valid_parameter_name(param_name):
                            parameters.append({
                                "name": param_name,
                                "source": "javascript",
                                "pattern": pattern_name,
                                "line": line_num,
                                "context": line.strip(),
                                "file": source_file or "unknown",
                                "confidence": self._calculate_js_confidence(pattern_name, param_name, line)
                            })
        
        return parameters
    
    def _parse_parameter_string(self, param_string: str) -> List[str]:
        """Parse function parameter string into individual parameter names."""
        # Handle destructuring and default parameters
        params = []
        
        # Split by comma and clean up
        parts = param_string.split(',')
        for part in parts:
            part = part.strip()
            
            # Handle destructuring: {name, age} or [x, y]
            if part.startswith('{') or part.startswith('['):
                # Extract names from destructuring
                destructured = re.findall(r'([a-zA-Z_$][a-zA-Z0-9_$]*)', part)
                params.extend(destructured)
            else:
                # Handle default parameters: name = 'default'
                if '=' in part:
                    part = part.split('=')[0].strip()
                
                # Extract parameter name
                param_match = re.match(r'([a-zA-Z_$][a-zA-Z0-9_$]*)', part)
                if param_match:
                    params.append(param_match.group(1))
        
        return params
    
    def _extract_json_keys_recursive(self, data: Any, keys: Set[str] = None) -> Set[str]:
        """Recursively extract all keys from JSON structure."""
        if keys is None:
            keys = set()
        
        if isinstance(data, dict):
            for key in data.keys():
                if isinstance(key, str):
                    keys.add(key)
                self._extract_json_keys_recursive(data[key], keys)
        elif isinstance(data, list):
            for item in data:
                self._extract_json_keys_recursive(item, keys)
        
        return keys
    
    def _extract_xml_elements_recursive(self, element: ET.Element, elements: Set[str] = None) -> Set[str]:
        """Recursively extract all element names from XML."""
        if elements is None:
            elements = set()
        
        # Add current element tag
        if element.tag and isinstance(element.tag, str):
            # Remove namespace if present
            tag_name = element.tag.split('}')[-1] if '}' in element.tag else element.tag
            elements.add(tag_name)
        
        # Add attribute names
        for attr_name in element.attrib.keys():
            if isinstance(attr_name, str):
                elements.add(attr_name)
        
        # Recurse into children
        for child in element:
            self._extract_xml_elements_recursive(child, elements)
        
        return elements
    
    def _is_valid_parameter_name(self, name: str) -> bool:
        """Check if a parameter name is valid and not a common false positive."""
        if not name or len(name) < 2:
            return False
        
        # Must start with letter or underscore
        if not re.match(r'^[a-zA-Z_]', name):
            return False
        
        # Filter out common false positives
        false_positives = {
            'var', 'let', 'const', 'function', 'return', 'if', 'else', 'for', 'while',
            'do', 'switch', 'case', 'break', 'continue', 'try', 'catch', 'finally',
            'true', 'false', 'null', 'undefined', 'this', 'new', 'typeof', 'instanceof',
            'length', 'push', 'pop', 'toString', 'valueOf', 'constructor', 'prototype'
        }
        
        return name.lower() not in false_positives
    
    def _calculate_js_confidence(self, pattern_name: str, param_name: str, context: str) -> float:
        """Calculate confidence score for JavaScript parameter extraction."""
        confidence = 0.5  # Base confidence
        
        # Pattern-based scoring
        pattern_scores = {
            "var_declaration": 0.5,
            "function_params": 0.9,
            "arrow_params": 0.9,
            "object_property": 0.7,
            "destructuring": 0.8,
            "form_field": 0.9,
            "property_access": 0.4
        }
        
        confidence = pattern_scores.get(pattern_name, 0.5)
        
        # Parameter name quality scoring
        if len(param_name) > 3 and '_' in param_name:
            confidence += 0.1
        if param_name.endswith(('_id', 'Id', 'ID')):
            confidence += 0.1
        if any(keyword in param_name.lower() for keyword in ['name', 'email', 'user', 'pass', 'token', 'key']):
            confidence += 0.2
        
        return min(confidence, 1.0)
    
    def _calculate_json_confidence(self, key: str) -> float:
        """Calculate confidence score for JSON key extraction."""
        confidence = 0.7  # Base confidence for JSON keys
        
        # Key quality scoring
        if len(key) > 3:
            confidence += 0.1
        if '_' in key or any(c.isupper() for c in key[1:]):  # snake_case or camelCase
            confidence += 0.1
        if any(keyword in key.lower() for keyword in ['name', 'email', 'user', 'pass', 'token', 'key']):
            confidence += 0.2
        
        return min(confidence, 1.0)
    
    def _calculate_xml_confidence(self, element_name: str) -> float:
        """Calculate confidence score for XML element extraction."""
        confidence = 0.6  # Base confidence for XML elements
        
        # Element name quality scoring
        if len(element_name) > 3:
            confidence += 0.1
        if any(keyword in element_name.lower() for keyword in ['name', 'email', 'user', 'field', 'param']):
            confidence += 0.2
        
        return min(confidence, 1.0)
    
    def _calculate_html_confidence(self, pattern_name: str, param_name: str) -> float:
        """Calculate confidence score for HTML parameter extraction."""
        # HTML form fields are high confidence
        pattern_scores = {
            "input_name": 0.9,
            "textarea_name": 0.9,
            "select_name": 0.9,
            "input_id": 0.7,
            "data_attr": 0.6
        }
        
        confidence = pattern_scores.get(pattern_name, 0.5)
        
        # Parameter name quality
        if any(keyword in param_name.lower() for keyword in ['name', 'email', 'user', 'pass', 'token']):
            confidence += 0.1
        
        return min(confidence, 1.0)
    
    def _deduplicate_parameters(self, parameters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Deduplicate parameters while preserving highest confidence entries."""
        param_map = {}
        
        for param in parameters:
            name = param["name"]
            if name not in param_map or param["confidence"] > param_map[name]["confidence"]:
                param_map[name] = param
        
        # Sort by confidence (highest first), then by name
        return sorted(param_map.values(), key=lambda p: (-p["confidence"], p["name"]))
