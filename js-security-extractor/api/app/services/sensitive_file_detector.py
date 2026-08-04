"""
Service for detecting sensitive file references in JavaScript code.
Implements B-008 - Sensitive File Reference Detection with strict noise controls.
"""

import re
import logging
from typing import List, Dict, Any, Set
from dataclasses import dataclass
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


@dataclass
class SensitiveFileReference:
    """Represents a detected sensitive file reference."""
    path: str
    confidence: str  # "high", "medium", "low"
    reason: str
    category: str
    line: int | None = None
    column: int | None = None
    context: str | None = None
    extractor: str = "sensitive_file_detector"


class SensitiveFileDetector:
    """Detects references to potentially sensitive files with low false-positive rates."""
    
    def __init__(self):
        self.max_candidate_path_length = 1024

        # High-confidence patterns - these have very low false positive rates
        self.high_confidence_patterns = {
            # Configuration files
            "config": [
                r'\.env(?:\.[a-zA-Z0-9_-]+)?',  # .env, .env.local, .env.production
                r'config\.(?:json|yaml|yml|xml|ini|toml)',
                r'settings\.(?:json|yaml|yml|xml|ini)',
                r'application\.(?:properties|yml|yaml)',
                r'web\.config',
                r'app\.config',
            ],
            
            # Backup and archive files
            "backup": [
                r'\.bak(?:\.[a-zA-Z0-9]+)?',
                r'\.backup',
                r'\.old',
                r'\.orig',
                r'\.save',
                r'\.tmp\.(?:js|css|html)',  # Temporary source files
                r'~$',  # Unix backup suffix
            ],
            
            # Key and certificate files
            "keys": [
                r'\.pem',
                r'\.key',
                r'\.crt',
                r'\.cert',
                r'\.p12',
                r'\.pfx',
                r'\.jks',
                r'id_rsa',
                r'id_dsa',
                r'id_ed25519',
                r'private[_-]?key',
            ],
            
            # Database and data files
            "database": [
                r'\.db',
                r'\.sqlite',
                r'\.sqlite3',
                r'dump\.sql',
                r'backup\.sql',
                r'database\.sql',
            ],
            
            # Version control and development files
            "development": [
                r'\.git/config',
                r'\.svn/',
                r'\.htaccess',
                r'\.htpasswd',
                r'composer\.json',
                r'package-lock\.json',
                r'yarn\.lock',
                r'Gemfile\.lock',
            ],
        }
        
        # Medium-confidence patterns - might have some false positives
        self.medium_confidence_patterns = {
            "config": [
                r'constants?\.(?:js|json)',
                r'globals?\.(?:js|json)', 
                r'secrets?\.(?:js|json)',
                r'credentials?\.(?:js|json)',
            ],
            
            "logs": [
                r'\.log',
                r'error\.txt',
                r'debug\.txt',
                r'access\.log',
            ],
        }
        
        # Low-confidence patterns - higher chance of false positives
        self.low_confidence_patterns = {
            "development": [
                r'test\.(?:js|json)',
                r'spec\.(?:js|json)',
                r'mock\.(?:js|json)',
            ],
        }
        
        # Patterns to suppress (bundler/build artifacts, common static assets)
        self.suppression_patterns = [
            r'node_modules/',
            r'dist/',
            r'build/',
            r'public/',
            r'static/',
            r'assets/',
            r'vendor/',
            r'\.min\.js',
            r'\.bundle\.js',
            r'\.chunk\.js',
            r'webpack.*\.js',
            r'[a-f0-9]{8,}\.(?:js|css)',  # Webpack hash naming
            r'main\.[a-f0-9]{8}\.js',     # CRA-style bundles
            r'runtime.*\.js',
            r'polyfills.*\.js',
        ]
        
        # Common file extensions that are typically not sensitive
        self.common_extensions = {
            'js', 'css', 'html', 'htm', 'png', 'jpg', 'jpeg', 'gif', 'svg', 
            'ico', 'woff', 'woff2', 'ttf', 'eot', 'pdf', 'txt', 'md'
        }
    
    def detect_sensitive_files(self, content: str, source_url: str = "unknown", 
                             include_low_confidence: bool = False) -> List[Dict[str, Any]]:
        """
        Detect sensitive file references in JavaScript content.
        
        Args:
            content: JavaScript source code
            source_url: URL/path of the source file
            include_low_confidence: Whether to include low-confidence detections
            
        Returns:
            List of sensitive file reference dictionaries
        """
        logger.info(f"Detecting sensitive files in {source_url}")
        
        findings = []
        
        # Extract potential file references from the content
        file_references = self._extract_file_references(content)
        
        for ref_info in file_references:
            path = ref_info["path"]
            line = ref_info.get("line")
            column = ref_info.get("column") 
            context = ref_info.get("context", "")
            
            # Check if this path should be suppressed
            if self._should_suppress(path):
                continue
            
            # Check against patterns in order of confidence
            detection = self._classify_file_path(path)
            
            if detection:
                confidence, reason, category = detection
                
                # Skip low confidence unless explicitly requested
                if confidence == "low" and not include_low_confidence:
                    continue
                
                finding = {
                    "path": path,
                    "confidence": confidence,
                    "reason": reason,
                    "category": category,
                    "line": line,
                    "column": column,
                    "context": context,
                    "extractor": "sensitive_file_detector"
                }
                
                findings.append(finding)
        
        # Sort by confidence (high first) and then by category
        confidence_order = {"high": 0, "medium": 1, "low": 2}
        findings.sort(key=lambda x: (confidence_order[x["confidence"]], x["category"], x["path"]))
        
        logger.info(f"Found {len(findings)} sensitive file references")
        return findings
    
    def _extract_file_references(self, content: str) -> List[Dict[str, Any]]:
        """Extract potential file references from JavaScript content."""
        references = []
        lines = content.split('\n')
        
        # Patterns to match file references in various contexts
        file_ref_patterns = [
            # String literals with file paths/URLs
            r'''(['"`])([^'"`]*\.[a-zA-Z0-9]{1,10}[^'"`]*)\1''',
            
            # require() and import statements
            r'''(?:require|import)\s*\(\s*['"`]([^'"`]+)['"`]\s*\)''',
            
            # URL constructor and fetch calls
            r'''(?:new\s+URL|fetch|axios\.get|\.get)\s*\(\s*['"`]([^'"`]+)['"`]''',
            
            # File path assignments
            r'''(?:path|file|url|src|href)\s*[:=]\s*['"`]([^'"`]+)['"`]''',
        ]
        
        for line_num, line in enumerate(lines, 1):
            for pattern in file_ref_patterns:
                matches = re.finditer(pattern, line, re.IGNORECASE)
                for match in matches:
                    # Get the file path (last capturing group)
                    path = match.group(match.lastindex) if match.lastindex else match.group(0)
                    
                    # Skip obviously non-file strings
                    if not self._looks_like_file_path(path):
                        continue
                    
                    references.append({
                        "path": path.strip(),
                        "line": line_num,
                        "column": match.start(),
                        "context": line.strip()[:200]  # Limit context length
                    })
        
        # Deduplicate while preserving line information
        seen = set()
        unique_refs = []
        for ref in references:
            key = ref["path"]
            if key not in seen:
                seen.add(key)
                unique_refs.append(ref)
        
        return unique_refs
    
    def _looks_like_file_path(self, path: str) -> bool:
        """Check if a string looks like a file path or URL."""
        if not path or len(path) < 3:
            return False

        # Reject pathological candidates from minified blobs.
        if len(path) > self.max_candidate_path_length:
            return False
            
        # Skip obvious non-file strings
        if path in ('', '/', '#', '?', 'GET', 'POST', 'PUT', 'DELETE'):
            return False
            
        # Skip single character strings
        if len(path) == 1:
            return False
        
        # Must contain a dot (for extension) or path separator
        if '.' not in path and '/' not in path and '\\' not in path:
            return False
            
        # Skip URLs that are clearly API endpoints (no file extension)
        if path.startswith(('http://', 'https://', '/api/', 'api/')):
            parsed = urlparse(path)
            path_part = parsed.path
            if '.' not in path_part.split('/')[-1]:  # No extension in last segment
                return False

        # Keep only URL/path-safe candidates; minified expressions often contain
        # operators that should never appear in file paths.
        if not re.fullmatch(r"[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%\\-]+", path):
            return False
        
        return True
    
    def _should_suppress(self, path: str) -> bool:
        """Check if a file path should be suppressed (bundler artifacts, etc.)."""
        path_lower = path.lower()
        
        for pattern in self.suppression_patterns:
            if re.search(pattern, path_lower):
                return True
                
        return False
    
    def _classify_file_path(self, path: str) -> tuple[str, str, str] | None:
        """
        Classify a file path by confidence and category.
        
        Returns:
            Tuple of (confidence, reason, category) or None if not sensitive
        """
        path_lower = path.lower()
        
        # Check high confidence patterns first
        for category, patterns in self.high_confidence_patterns.items():
            for pattern in patterns:
                if re.search(pattern, path_lower):
                    return ("high", f"Matches {category} pattern: {pattern}", category)
        
        # Check medium confidence patterns
        for category, patterns in self.medium_confidence_patterns.items():
            for pattern in patterns:
                if re.search(pattern, path_lower):
                    return ("medium", f"Matches {category} pattern: {pattern}", category)
        
        # Check low confidence patterns
        for category, patterns in self.low_confidence_patterns.items():
            for pattern in patterns:
                if re.search(pattern, path_lower):
                    return ("low", f"Matches {category} pattern: {pattern}", category)
        
        return None
