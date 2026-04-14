import re
import logging
from typing import Dict, List, Any, Optional

from ..config import settings

logger = logging.getLogger(__name__)


class SmartAnalysisTriggers:
    """
    Service for determining when files should be automatically analyzed
    based on content heuristics and characteristics.
    """
    
    def __init__(self):
        self.api_patterns = [
            r'/api/',
            r'/v\d+/',
            r'\.json\b',
            r'fetch\s*\(',
            r'axios\.',
            r'XMLHttpRequest',
            r'\.post\s*\(',
            r'\.get\s*\(',
            r'endpoint',
            r'graphql',
            r'/rest/',
        ]
        
        self.secret_patterns = [
            r'["\']([a-zA-Z0-9]{32,})["\']',  # Long strings that could be keys
            r'api[_\-]?key',
            r'secret',
            r'token',
            r'password',
            r'auth[_\-]?key',
            r'access[_\-]?token',
            r'bearer',
            r'jwt',
            r'oauth',
            r'client[_\-]?secret',
        ]
        
        # Compiled patterns for performance
        self.api_regex = [re.compile(pattern, re.IGNORECASE) for pattern in self.api_patterns]
        self.secret_regex = [re.compile(pattern, re.IGNORECASE) for pattern in self.secret_patterns]
    
    def should_trigger_analysis(
        self, 
        content: str, 
        file_metadata: Dict[str, Any], 
        sourcemap_status: Optional[str] = None,
        manual_analysis_requested: bool = False
    ) -> Dict[str, Any]:
        """
        Determine if smart analysis should be triggered for a file.
        
        Returns:
            Dictionary with trigger decision and reasoning
        """
        if not settings.smart_analysis_enabled:
            return {
                "trigger": False,
                "reason": "smart_analysis_disabled",
                "criteria_met": []
            }
        
        # Manual analysis always takes precedence
        if manual_analysis_requested:
            return {
                "trigger": True,
                "reason": "manual_request",
                "criteria_met": ["manual_request"]
            }
        
        criteria_met = []
        
        # Check file size threshold
        file_size = len(content.encode('utf-8'))
        if file_size >= settings.smart_analysis_min_file_size:
            criteria_met.append(f"large_file_{file_size}_bytes")
        
        # Check if file has processed sourcemap
        if (settings.smart_analysis_with_sourcemaps and 
            sourcemap_status in ["completed", "completed_limited"]):
            criteria_met.append("has_sourcemap")
        
        # Check for API patterns
        api_pattern_count = self._count_api_patterns(content)
        if api_pattern_count >= settings.smart_analysis_api_pattern_threshold:
            criteria_met.append(f"api_patterns_{api_pattern_count}")
        
        # Check for potential secrets/keys
        secret_pattern_count = self._count_secret_patterns(content)
        if secret_pattern_count >= settings.smart_analysis_secret_pattern_threshold:
            criteria_met.append(f"secret_patterns_{secret_pattern_count}")
        
        # Check if JavaScript appears minified
        if self._is_minified_js(content):
            criteria_met.append("minified_js")
        
        # Trigger if any criteria are met
        should_trigger = len(criteria_met) > 0
        
        if should_trigger:
            logger.info(f"Smart analysis triggered for file: {criteria_met}")
        
        return {
            "trigger": should_trigger,
            "reason": "smart_heuristics" if should_trigger else "no_criteria_met",
            "criteria_met": criteria_met,
            "file_size": file_size,
            "api_patterns": api_pattern_count,
            "secret_patterns": secret_pattern_count
        }
    
    def _count_api_patterns(self, content: str) -> int:
        """Count number of API-related patterns in content"""
        count = 0
        for pattern in self.api_regex:
            matches = pattern.findall(content)
            count += len(matches)
        return count
    
    def _count_secret_patterns(self, content: str) -> int:
        """Count number of potential secret patterns in content"""
        count = 0
        for pattern in self.secret_regex:
            matches = pattern.findall(content)
            count += len(matches)
        return count
    
    def _is_minified_js(self, content: str) -> bool:
        """
        Detect if JavaScript appears to be minified by checking
        the ratio of long lines to total lines
        """
        lines = content.split('\n')
        if len(lines) < 5:  # Too small to analyze
            return False
        
        long_lines = sum(1 for line in lines if len(line) > 80)
        long_line_ratio = long_lines / len(lines)
        
        return long_line_ratio >= settings.smart_analysis_minified_js_threshold
    
    def get_trigger_summary(self) -> Dict[str, Any]:
        """Return current trigger configuration for debugging"""
        return {
            "enabled": settings.smart_analysis_enabled,
            "min_file_size": settings.smart_analysis_min_file_size,
            "with_sourcemaps": settings.smart_analysis_with_sourcemaps,
            "api_pattern_threshold": settings.smart_analysis_api_pattern_threshold,
            "secret_pattern_threshold": settings.smart_analysis_secret_pattern_threshold,
            "minified_js_threshold": settings.smart_analysis_minified_js_threshold,
            "api_patterns_count": len(self.api_patterns),
            "secret_patterns_count": len(self.secret_patterns)
        }