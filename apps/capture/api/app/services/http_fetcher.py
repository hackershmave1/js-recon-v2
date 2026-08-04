import asyncio
import logging
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx

from ..config import settings

logger = logging.getLogger(__name__)


class FetchResult:
    """Result of an HTTP fetch operation."""
    
    def __init__(
        self,
        success: bool,
        status_code: Optional[int] = None,
        content: Optional[str] = None,
        content_type: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        final_url: Optional[str] = None,
        content_length: Optional[int] = None,
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
        retry_count: int = 0,
    ):
        self.success = success
        self.status_code = status_code
        self.content = content
        self.content_type = content_type
        self.headers = headers or {}
        self.final_url = final_url
        self.content_length = content_length
        self.error_type = error_type
        self.error_message = error_message
        self.retry_count = retry_count

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for backward compatibility."""
        result = {
            "success": self.success,
            "statusCode": self.status_code,
            "content": self.content,
            "contentType": self.content_type,
            "headers": self.headers,
            "finalUrl": self.final_url,
            "contentLength": self.content_length,
            "retryCount": self.retry_count,
        }
        
        if not self.success:
            result["failureReason"] = self.error_type
            result["error"] = self.error_message
        
        return result


class RobustHttpFetcher:
    """
    Robust HTTP fetcher with retry logic, size caps, and binary content detection.
    
    Features:
    - Configurable retry policy for transient errors (429, 5xx)
    - Response size caps to prevent memory exhaustion
    - Binary content detection and short-circuiting
    - Timeout and connection handling
    - Detailed error classification
    """
    
    def __init__(
        self,
        max_retries: Optional[int] = None,
        retry_backoff: Optional[float] = None,
        max_response_size: Optional[int] = None,
        timeout_seconds: Optional[int] = None,
        connect_timeout_seconds: Optional[int] = None,
        user_agent: Optional[str] = None,
    ):
        self.max_retries = max_retries or settings.fetch_max_retries
        self.retry_backoff = retry_backoff or settings.fetch_retry_backoff
        self.max_response_size = max_response_size or settings.fetch_max_response_size
        self.timeout_seconds = timeout_seconds or settings.fetch_timeout_seconds
        self.connect_timeout_seconds = connect_timeout_seconds or settings.fetch_connect_timeout_seconds
        self.user_agent = user_agent or settings.fetch_user_agent
        self.retry_enabled = settings.fetch_retry_enabled

    async def fetch_text(
        self, 
        url: str, 
        headers: Optional[Dict[str, str]] = None,
        check_content_type: bool = True
    ) -> FetchResult:
        """
        Fetch text content from URL with retry logic and hardening.
        
        Args:
            url: URL to fetch
            headers: Additional headers to send
            check_content_type: Whether to validate content type for text/JS
            
        Returns:
            FetchResult with success status and content or error details
        """
        if not url or not url.strip():
            return FetchResult(
                success=False,
                error_type="invalid_url",
                error_message="URL is empty or invalid"
            )

        # Validate URL format
        try:
            parsed = urlparse(url.strip())
            if not parsed.scheme or not parsed.netloc:
                return FetchResult(
                    success=False,
                    error_type="invalid_url",
                    error_message="URL missing scheme or netloc"
                )
        except Exception as e:
            return FetchResult(
                success=False,
                error_type="invalid_url", 
                error_message=f"URL parsing failed: {e}"
            )

        retry_count = 0
        last_exception = None
        
        while retry_count <= self.max_retries:
            try:
                result = await self._attempt_fetch(url, headers, check_content_type)
                
                # Return on success or non-retryable failure
                if result.success or not self._should_retry(result):
                    result.retry_count = retry_count
                    return result
                
                # Prepare for retry
                if retry_count < self.max_retries:
                    delay = self.retry_backoff * (2 ** retry_count)
                    logger.debug(f"Retrying {url} in {delay:.1f}s (attempt {retry_count + 1}/{self.max_retries})")
                    await asyncio.sleep(delay)
                
                retry_count += 1
                last_exception = result
                
            except Exception as e:
                logger.warning(f"Unexpected error fetching {url}: {e}")
                last_exception = FetchResult(
                    success=False,
                    error_type="unexpected_error",
                    error_message=str(e)
                )
                break
        
        # Return last failure after exhausting retries
        if last_exception:
            last_exception.retry_count = retry_count
            return last_exception
        
        return FetchResult(
            success=False,
            error_type="retry_exhausted",
            error_message=f"Failed after {retry_count} retries",
            retry_count=retry_count
        )

    async def _attempt_fetch(
        self, 
        url: str, 
        headers: Optional[Dict[str, str]], 
        check_content_type: bool
    ) -> FetchResult:
        """Single fetch attempt without retry logic."""
        
        # Prepare headers
        request_headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/plain, application/javascript, text/javascript, application/x-javascript, */*",
        }
        if headers:
            request_headers.update(headers)
        
        # Configure timeouts
        timeout = httpx.Timeout(
            connect=self.connect_timeout_seconds,
            read=self.timeout_seconds,
            write=self.timeout_seconds,
            pool=self.timeout_seconds
        )
        
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, 
                timeout=timeout, 
                headers=request_headers,
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5)
            ) as client:
                
                # Stream the response to check size and content type early
                async with client.stream('GET', url) as response:
                    
                    # Check HTTP status
                    if response.status_code >= 400:
                        error_type = self._classify_http_error(response.status_code)
                        return FetchResult(
                            success=False,
                            status_code=response.status_code,
                            error_type=error_type,
                            error_message=f"HTTP {response.status_code}",
                            headers={k.lower(): v for k, v in response.headers.items()}
                        )
                    
                    # Check content type if requested
                    content_type = response.headers.get("content-type", "").lower()
                    if check_content_type and not self._is_text_content_type(content_type):
                        return FetchResult(
                            success=False,
                            status_code=response.status_code,
                            content_type=content_type,
                            error_type="binary_content",
                            error_message=f"Non-text content type: {content_type}",
                            headers={k.lower(): v for k, v in response.headers.items()}
                        )
                    
                    # Check content length header
                    content_length_header = response.headers.get("content-length")
                    if content_length_header:
                        try:
                            declared_length = int(content_length_header)
                            if declared_length > self.max_response_size:
                                return FetchResult(
                                    success=False,
                                    status_code=response.status_code,
                                    error_type="response_too_large",
                                    error_message=f"Response too large: {declared_length} bytes (max: {self.max_response_size})",
                                    headers={k.lower(): v for k, v in response.headers.items()}
                                )
                        except ValueError:
                            pass  # Invalid content-length header, continue
                    
                    # Read content with size checking
                    content_chunks = []
                    total_size = 0
                    
                    async for chunk in response.aiter_bytes(8192):
                        total_size += len(chunk)
                        if total_size > self.max_response_size:
                            return FetchResult(
                                success=False,
                                status_code=response.status_code,
                                error_type="response_too_large", 
                                error_message=f"Response exceeded size limit: {total_size} bytes (max: {self.max_response_size})",
                                headers={k.lower(): v for k, v in response.headers.items()}
                            )
                        content_chunks.append(chunk)
                    
                    # Decode content
                    try:
                        content_bytes = b''.join(content_chunks)
                        content = content_bytes.decode('utf-8', errors='ignore')
                    except Exception as e:
                        return FetchResult(
                            success=False,
                            status_code=response.status_code,
                            error_type="decode_error",
                            error_message=f"Failed to decode response: {e}",
                            headers={k.lower(): v for k, v in response.headers.items()}
                        )
                    
                    # Final binary content check on decoded content
                    if check_content_type and self._looks_like_binary(content):
                        return FetchResult(
                            success=False,
                            status_code=response.status_code,
                            error_type="binary_content",
                            error_message="Content appears to be binary data",
                            content_type=content_type,
                            headers={k.lower(): v for k, v in response.headers.items()}
                        )
                    
                    return FetchResult(
                        success=True,
                        status_code=response.status_code,
                        content=content,
                        content_type=content_type,
                        headers={k.lower(): v for k, v in response.headers.items()},
                        final_url=str(response.url),
                        content_length=len(content_bytes)
                    )
                    
        except httpx.TimeoutException:
            return FetchResult(
                success=False,
                error_type="fetch_timeout",
                error_message=f"Request timed out after {self.timeout_seconds}s"
            )
        except httpx.ConnectTimeout:
            return FetchResult(
                success=False,
                error_type="connect_timeout",
                error_message=f"Connection timed out after {self.connect_timeout_seconds}s"
            )
        except httpx.HTTPStatusError as e:
            error_type = self._classify_http_error(e.response.status_code)
            return FetchResult(
                success=False,
                status_code=e.response.status_code,
                error_type=error_type,
                error_message=f"HTTP {e.response.status_code}: {e}",
                headers={k.lower(): v for k, v in e.response.headers.items()}
            )
        except httpx.RequestError as e:
            return FetchResult(
                success=False,
                error_type="network_error",
                error_message=f"Network error: {e}"
            )

    def _should_retry(self, result: FetchResult) -> bool:
        """Determine if a failed request should be retried."""
        if not self.retry_enabled:
            return False
        
        # Retry on specific HTTP status codes
        if result.status_code in (429, 500, 502, 503, 504):
            return True
        
        # Retry on specific error types
        if result.error_type in ("fetch_timeout", "connect_timeout", "network_error"):
            return True
        
        return False

    def _classify_http_error(self, status_code: int) -> str:
        """Classify HTTP error status codes."""
        if 400 <= status_code < 500:
            return "fetch_4xx"
        elif 500 <= status_code < 600:
            return "fetch_5xx"
        else:
            return "fetch_error"

    def _is_text_content_type(self, content_type: str) -> bool:
        """Check if content type indicates text content."""
        if not content_type:
            return True  # Assume text if no content type
        
        text_types = [
            "text/",
            "application/javascript",
            "application/x-javascript", 
            "application/json",
            "application/ecmascript"
        ]
        
        return any(content_type.startswith(t) for t in text_types)

    def _looks_like_binary(self, content: str, sample_size: int = 1024) -> bool:
        """Detect if content looks like binary data."""
        if not content:
            return False
        
        # Sample first part of content
        sample = content[:sample_size]
        
        # Check for high ratio of non-printable characters
        non_printable = sum(1 for c in sample if ord(c) < 32 and c not in '\r\n\t')
        non_printable_ratio = non_printable / len(sample) if sample else 0
        
        # If more than 10% non-printable, likely binary
        return non_printable_ratio > 0.1


# Global instance for easy usage
robust_fetcher = RobustHttpFetcher()