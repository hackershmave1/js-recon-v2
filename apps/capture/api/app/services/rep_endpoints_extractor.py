import logging
import re
from typing import Any
from urllib.parse import urlparse

from .regex_utils import chunked_regex
from .endpoint_sanitizer import EndpointSanitizer
from ..config import settings

logger = logging.getLogger(__name__)

HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]

ENDPOINT_PATTERNS: dict[str, re.Pattern[str]] = {
    "apiPath": re.compile(r"[\"'`](\/api\/[a-zA-Z0-9_\-\/{}:]+)[\"'`]"),
    "versionedPath": re.compile(r"[\"'`](\/v\d+\/[a-zA-Z0-9_\-\/{}:]+)[\"'`]"),
    "fullUrl": re.compile(r"[\"'`](https?:\/\/[a-zA-Z0-9\-._~:\/?#[\]@!$&'()*+,;=%]+)[\"'`]"),
    "relativePath": re.compile(r"[\"'`](\/[a-zA-Z0-9_\-]+(?:\/[a-zA-Z0-9_\-{}:]+)+)[\"'`]"),
    "graphqlPath": re.compile(r"[\"'`](\/graphql|\/gql)[\"'`]", re.IGNORECASE),
    "fetchCall": re.compile(r"(?:fetch|axios)\s*\(\s*[\"'`]([^\"'`]+)[\"'`]"),
    "axiosMethod": re.compile(r"axios\.(get|post|put|patch|delete|head|options)\s*\(\s*[\"'`]([^\"'`]+)[\"'`]", re.IGNORECASE),
    "templateUrl": re.compile(r"`([^`]*(?:https?:\/\/|\/api\/|\/v\d+/)[^`]*)`"),
    "restEndpoint": re.compile(
        r"[\"'`](\/(?:users|auth|login|logout|register|profile|settings|posts|comments|products|orders|payments|upload|download|search|items|entities|resources)(?:\/[a-zA-Z0-9_\-{}:]*)?(?:\/[a-zA-Z0-9_\-{}:]+)*)[\"'`]"
    ),
}

FALSE_POSITIVES = (
    "//",
    '/\\"',
    "/\\",
    "/node_modules/",
    "/webpack/",
    "/dist/",
    "/build/",
    "/__",
    "/static/",
    "/public/",
    "/images/",
    "/fonts/",
    "/styles/",
    "/scripts/",
)


class RepEndpointsExtractor:
    def __init__(self):
        """Initialize the extractor with optional sanitization."""
        self.sanitizer = None
        if settings.endpoint_sanitization_enabled:
            self.sanitizer = EndpointSanitizer(
                enable_domain_filtering=settings.endpoint_filter_domains,
                enable_extension_filtering=settings.endpoint_filter_extensions
            )
    
    def extract(self, content: str, source_file: str | None = None) -> list[dict[str, Any]]:
        if not content:
            return []

        results: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        base_url = self._base_url_from_source(source_file)

        # Check if we should use chunked processing
        use_chunked = chunked_regex.should_chunk(content)
        
        if use_chunked:
            logger.info(f"Using chunked regex processing for large content ({len(content)} chars)")
            results = self._extract_chunked(content, source_file, base_url, seen)
        else:
            results = self._extract_standard(content, source_file, base_url, seen)

        # Apply endpoint sanitization if enabled
        if self.sanitizer and results:
            results = self.sanitizer.sanitize_endpoints(results)
        
        results.sort(key=lambda item: int(item.get("confidence_score", 0)), reverse=True)
        return results
    
    def _extract_standard(self, content: str, source_file: str | None, base_url: str, seen: set) -> list[dict[str, Any]]:
        """Standard extraction for smaller files"""
        results = []
        
        for pattern_name, pattern in ENDPOINT_PATTERNS.items():
            for match in pattern.finditer(content):
                endpoint = match.group(1) if match.groups() else match.group(0)
                if pattern_name == "axiosMethod" and len(match.groups()) > 1:
                    endpoint = match.group(2)
                if not endpoint:
                    continue

                result = self._process_endpoint_match(
                    endpoint, pattern_name, content, match.start(), 
                    source_file, base_url, seen
                )
                if result:
                    results.append(result)
        
        return results
    
    def _extract_chunked(self, content: str, source_file: str | None, base_url: str, seen: set) -> list[dict[str, Any]]:
        """Chunked extraction for large files"""
        results = []
        
        for pattern_name, pattern in ENDPOINT_PATTERNS.items():
            try:
                # Process pattern across all chunks
                for chunk_content, start_offset, end_offset in chunked_regex.create_chunks(content):
                    for match in pattern.finditer(chunk_content):
                        endpoint = match.group(1) if match.groups() else match.group(0)
                        if pattern_name == "axiosMethod" and len(match.groups()) > 1:
                            endpoint = match.group(2)
                        if not endpoint:
                            continue
                        
                        # Adjust match position to global content position
                        global_position = start_offset + match.start()
                        
                        result = self._process_endpoint_match(
                            endpoint, pattern_name, content, global_position,
                            source_file, base_url, seen
                        )
                        if result:
                            results.append(result)
                            
            except Exception as e:
                logger.warning(f"Error processing pattern {pattern_name} in chunked mode: {e}")
                continue
        
        return results
    
    def _process_endpoint_match(self, endpoint: str, pattern_name: str, content: str, 
                              position: int, source_file: str | None, base_url: str, 
                              seen: set) -> dict[str, Any] | None:
        """Process a single endpoint match and return result dict or None"""
        endpoint = self._normalize_endpoint(endpoint)
        if not self._is_valid_endpoint(endpoint):
            return None

        normalized_source = self._normalize_source_file(source_file)
        dedupe_key = (endpoint, normalized_source)
        if dedupe_key in seen:
            return None
        seen.add(dedupe_key)

        context = self._context(content, position, len(endpoint), width=100)
        method = self._extract_method(context, endpoint)
        confidence_score = self._calculate_confidence(endpoint, method, context)
        if confidence_score < 30:
            return None

        line, column = self._line_col(content, position)
        confidence = self._to_confidence_label(confidence_score)
        
        return {
            "url": endpoint,
            "endpoint": endpoint,
            "method": method,
            "type": pattern_name,
            "patternType": pattern_name,
            "confidence": confidence,
            "confidence_score": confidence_score,
            "extractor": "rep_endpoint_extractor",
            "file": source_file or "unknown",
            "source_file": source_file or "unknown",
            "base_url": base_url,
            "line": line,
            "column": column,
            "context": context,
        }

    def _base_url_from_source(self, source_file: str | None) -> str:
        if not source_file:
            return ""
        try:
            parsed = urlparse(source_file)
            if parsed.scheme and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}"
            return ""
        except Exception:
            return ""

    def _normalize_source_file(self, source_file: str | None) -> str:
        if not source_file:
            return ""
        try:
            parsed = urlparse(source_file)
            if parsed.scheme and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        except Exception:
            pass
        return source_file.split("?", 1)[0].split("#", 1)[0]

    def _normalize_endpoint(self, endpoint: str) -> str:
        cleaned = endpoint.replace('"', "").replace("'", "").replace("`", "").strip()
        return cleaned.split("?", 1)[0].strip()

    def _is_valid_endpoint(self, endpoint: str) -> bool:
        if not endpoint or len(endpoint) < 3:
            return False
        if not (endpoint.startswith("/") or endpoint.startswith("http")):
            return False
        return not any(token in endpoint for token in FALSE_POSITIVES)

    def _extract_method(self, context: str, endpoint: str) -> str:
        axios_match = re.search(r"axios\.(get|post|put|patch|delete|head|options)", context, re.IGNORECASE)
        if axios_match:
            return axios_match.group(1).upper()

        fetch_match = re.search(
            r"method\s*:\s*[\"'`](GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)[\"'`]",
            context,
            re.IGNORECASE,
        )
        if fetch_match:
            return fetch_match.group(1).upper()

        for method in HTTP_METHODS:
            if re.search(rf"[\"'`]{method}[\"'`]", context, re.IGNORECASE):
                return method

        if "{id}" in endpoint or ":id" in endpoint or re.search(r"/\d+", endpoint):
            return "GET"
        if any(token in endpoint for token in ("/login", "/register", "/upload", "/create")):
            return "POST"
        if any(token in endpoint for token in ("/update", "/edit")):
            return "PUT"
        if any(token in endpoint for token in ("/delete", "/remove")):
            return "DELETE"
        return "GET"

    def _calculate_confidence(self, endpoint: str, method: str, context: str) -> int:
        confidence = 50
        if endpoint.startswith("/api/"):
            confidence += 30
        if endpoint.startswith("/v1/") or endpoint.startswith("/v2/"):
            confidence += 25
        if endpoint in {"/graphql", "/gql"}:
            confidence += 30
        if method != "GET" or "method" in context:
            confidence += 15
        if "{" in endpoint or ":" in endpoint:
            confidence += 10
        if re.search(r"/(users|auth|login|posts|products|orders)", endpoint):
            confidence += 15
        if endpoint.startswith("http"):
            confidence += 20

        if len(endpoint) < 4:
            confidence -= 20
        if "/" not in endpoint:
            confidence -= 15
        if re.search(r"\.(js|css|png|jpg|jpeg|gif|svg|ico|woff|ttf|eot)$", endpoint, re.IGNORECASE):
            confidence -= 40

        return max(0, min(100, confidence))

    def _to_confidence_label(self, score: int) -> str:
        if score >= 80:
            return "high"
        if score >= 55:
            return "medium"
        return "low"

    def _line_col(self, content: str, index: int) -> tuple[int, int]:
        line = content.count("\n", 0, index) + 1
        previous_newline = content.rfind("\n", 0, index)
        column = index + 1 if previous_newline == -1 else index - previous_newline
        return line, max(1, column)

    def _context(self, content: str, start: int, length: int, width: int = 80) -> str:
        begin = max(0, start - width)
        end = min(len(content), start + length + width)
        return content[begin:end]
