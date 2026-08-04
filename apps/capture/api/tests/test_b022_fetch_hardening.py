"""
Tests for B-022 - Fetch Hardening for URL/SourceMap Retrieval

Tests the RobustHttpFetcher service and its integration with existing fetch workflows.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from app.services.http_fetcher import RobustHttpFetcher, FetchResult


class TestRobustHttpFetcher:
    """Test the RobustHttpFetcher service directly."""

    def setup_method(self):
        """Setup fresh fetcher for each test."""
        self.fetcher = RobustHttpFetcher(
            max_retries=2,
            retry_backoff=0.1,  # Fast tests
            max_response_size=1024 * 1024,  # 1MB for tests
            timeout_seconds=5,
            connect_timeout_seconds=2
        )

    @pytest.mark.asyncio
    async def test_successful_fetch(self):
        """Test successful HTTP fetch with valid response."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/javascript", "content-length": "100"}
        mock_response.url = "https://example.com/app.js"
        
        # Mock the streaming response
        async def mock_aiter_bytes(chunk_size):
            yield b"console.log('hello');"
        
        mock_response.aiter_bytes = mock_aiter_bytes
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_context = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_context
            mock_context.stream.return_value.__aenter__.return_value = mock_response
            
            result = await self.fetcher.fetch_text("https://example.com/app.js")
            
            assert result.success is True
            assert result.status_code == 200
            assert result.content == "console.log('hello');"
            assert result.content_type == "application/javascript"
            assert result.final_url == "https://example.com/app.js"

    @pytest.mark.asyncio
    async def test_retry_on_5xx_errors(self):
        """Test retry logic for 5xx server errors."""
        mock_response_fail = MagicMock()
        mock_response_fail.status_code = 503
        mock_response_fail.headers = {"content-type": "text/html"}
        
        mock_response_success = MagicMock()
        mock_response_success.status_code = 200
        mock_response_success.headers = {"content-type": "application/javascript"}
        mock_response_success.url = "https://example.com/app.js"
        
        async def mock_success_aiter_bytes(chunk_size):
            yield b"console.log('retry worked');"
        
        mock_response_success.aiter_bytes = mock_success_aiter_bytes
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_context = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_context
            
            # First call fails, second succeeds
            mock_context.stream.return_value.__aenter__.side_effect = [
                mock_response_fail,
                mock_response_success
            ]
            
            result = await self.fetcher.fetch_text("https://example.com/app.js")
            
            assert result.success is True
            assert result.retry_count == 1
            assert result.content == "console.log('retry worked');"

    @pytest.mark.asyncio
    async def test_retry_exhaustion_on_persistent_5xx(self):
        """Test that retries are exhausted on persistent server errors."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.headers = {"content-type": "text/html"}
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_context = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_context
            mock_context.stream.return_value.__aenter__.return_value = mock_response
            
            result = await self.fetcher.fetch_text("https://example.com/app.js")
            
            assert result.success is False
            assert result.error_type == "fetch_5xx"
            assert result.retry_count == 2  # max_retries

    @pytest.mark.asyncio
    async def test_no_retry_on_4xx_errors(self):
        """Test that 4xx errors are not retried."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.headers = {"content-type": "text/html"}
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_context = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_context
            mock_context.stream.return_value.__aenter__.return_value = mock_response
            
            result = await self.fetcher.fetch_text("https://example.com/app.js")
            
            assert result.success is False
            assert result.error_type == "fetch_4xx"
            assert result.retry_count == 0  # No retry

    @pytest.mark.asyncio
    async def test_response_size_cap_from_header(self):
        """Test response size cap enforcement from Content-Length header."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {
            "content-type": "application/javascript",
            "content-length": str(2 * 1024 * 1024)  # 2MB, exceeds 1MB test limit
        }
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_context = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_context
            mock_context.stream.return_value.__aenter__.return_value = mock_response
            
            result = await self.fetcher.fetch_text("https://example.com/app.js")
            
            assert result.success is False
            assert result.error_type == "response_too_large"
            assert "Response too large" in result.error_message

    @pytest.mark.asyncio
    async def test_response_size_cap_during_streaming(self):
        """Test response size cap enforcement during content streaming."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/javascript"}
        
        # Mock streaming large content
        async def mock_aiter_bytes(chunk_size):
            # Yield chunks that exceed size limit
            for i in range(200):  # 200 * 8KB = 1.6MB > 1MB limit
                yield b"x" * 8192
        
        mock_response.aiter_bytes = mock_aiter_bytes
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_context = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_context
            mock_context.stream.return_value.__aenter__.return_value = mock_response
            
            result = await self.fetcher.fetch_text("https://example.com/app.js")
            
            assert result.success is False
            assert result.error_type == "response_too_large"
            assert "exceeded size limit" in result.error_message

    @pytest.mark.asyncio
    async def test_binary_content_detection_by_content_type(self):
        """Test binary content detection via Content-Type header."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "image/png"}
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_context = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_context
            mock_context.stream.return_value.__aenter__.return_value = mock_response
            
            result = await self.fetcher.fetch_text("https://example.com/app.js")
            
            assert result.success is False
            assert result.error_type == "binary_content"
            assert "Non-text content type" in result.error_message

    @pytest.mark.asyncio
    async def test_binary_content_detection_by_content_analysis(self):
        """Test binary content detection by analyzing content."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/octet-stream"}
        mock_response.url = "https://example.com/app.js"
        
        # Mock binary content with many non-printable characters
        binary_content = b"\x00\x01\x02\x03" * 300  # Lots of non-printable chars
        
        async def mock_aiter_bytes(chunk_size):
            yield binary_content
        
        mock_response.aiter_bytes = mock_aiter_bytes
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_context = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_context
            mock_context.stream.return_value.__aenter__.return_value = mock_response
            
            result = await self.fetcher.fetch_text("https://example.com/app.js", check_content_type=False)
            
            assert result.success is False
            assert result.error_type == "binary_content"
            assert "appears to be binary" in result.error_message

    @pytest.mark.asyncio
    async def test_timeout_handling(self):
        """Test timeout error handling and retry."""
        with patch('httpx.AsyncClient') as mock_client:
            mock_context = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_context
            mock_context.stream.side_effect = httpx.TimeoutException("Request timed out")
            
            result = await self.fetcher.fetch_text("https://example.com/app.js")
            
            assert result.success is False
            assert result.error_type == "fetch_timeout"
            assert "timed out" in result.error_message.lower()
            assert result.retry_count == 2  # Should retry timeouts

    @pytest.mark.asyncio
    async def test_invalid_url_handling(self):
        """Test handling of invalid URLs."""
        test_cases = [
            "",
            "   ",
            "not-a-url",
            "ftp://example.com",  # Missing scheme validation
            "://example.com",     # Missing scheme
        ]
        
        for invalid_url in test_cases:
            result = await self.fetcher.fetch_text(invalid_url)
            assert result.success is False
            assert result.error_type == "invalid_url"

    @pytest.mark.asyncio 
    async def test_content_type_bypass(self):
        """Test bypassing content type checks when disabled."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "image/png"}  # Binary type
        mock_response.url = "https://example.com/app.js"
        
        async def mock_aiter_bytes(chunk_size):
            yield b"console.log('test');"  # Valid JS content
        
        mock_response.aiter_bytes = mock_aiter_bytes
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_context = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_context
            mock_context.stream.return_value.__aenter__.return_value = mock_response
            
            result = await self.fetcher.fetch_text(
                "https://example.com/app.js", 
                check_content_type=False
            )
            
            assert result.success is True
            assert result.content == "console.log('test');"

    @pytest.mark.asyncio
    async def test_network_error_retry(self):
        """Test retry on network errors."""
        with patch('httpx.AsyncClient') as mock_client:
            mock_context = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_context
            
            # First call network error, second succeeds
            mock_response_success = MagicMock()
            mock_response_success.status_code = 200
            mock_response_success.headers = {"content-type": "application/javascript"}
            mock_response_success.url = "https://example.com/app.js"
            
            async def mock_success_aiter_bytes(chunk_size):
                yield b"console.log('success after retry');"
            
            mock_response_success.aiter_bytes = mock_success_aiter_bytes
            
            mock_context.stream.side_effect = [
                httpx.RequestError("Connection failed"),
                mock_context.stream.return_value.__aenter__.return_value.__aexit__.return_value
            ]
            mock_context.stream.return_value.__aenter__.return_value = mock_response_success
            
            # Simplify the mock to avoid complex async context manager issues
            with patch.object(self.fetcher, '_attempt_fetch') as mock_attempt:
                mock_attempt.side_effect = [
                    FetchResult(success=False, error_type="network_error", error_message="Connection failed"),
                    FetchResult(success=True, content="console.log('success after retry');", status_code=200)
                ]
                
                result = await self.fetcher.fetch_text("https://example.com/app.js")
                
                assert result.success is True
                assert result.retry_count == 1

    def test_fetch_result_to_dict(self):
        """Test FetchResult to_dict conversion for backward compatibility."""
        # Success case
        result = FetchResult(
            success=True,
            status_code=200,
            content="test content",
            content_type="application/javascript",
            headers={"x-custom": "value"},
            final_url="https://example.com/final.js",
            content_length=100,
            retry_count=1
        )
        
        data = result.to_dict()
        
        assert data["success"] is True
        assert data["statusCode"] == 200
        assert data["content"] == "test content"
        assert data["contentType"] == "application/javascript"
        assert data["headers"]["x-custom"] == "value"
        assert data["finalUrl"] == "https://example.com/final.js"
        assert data["contentLength"] == 100
        assert data["retryCount"] == 1
        assert "failureReason" not in data
        assert "error" not in data
        
        # Failure case
        result = FetchResult(
            success=False,
            status_code=404,
            error_type="fetch_4xx",
            error_message="Not found",
            retry_count=0
        )
        
        data = result.to_dict()
        
        assert data["success"] is False
        assert data["statusCode"] == 404
        assert data["failureReason"] == "fetch_4xx"
        assert data["error"] == "Not found"
        assert data["retryCount"] == 0

    def test_error_classification(self):
        """Test HTTP error status code classification."""
        assert self.fetcher._classify_http_error(400) == "fetch_4xx"
        assert self.fetcher._classify_http_error(404) == "fetch_4xx"
        assert self.fetcher._classify_http_error(499) == "fetch_4xx"
        assert self.fetcher._classify_http_error(500) == "fetch_5xx"
        assert self.fetcher._classify_http_error(503) == "fetch_5xx"
        assert self.fetcher._classify_http_error(599) == "fetch_5xx"
        assert self.fetcher._classify_http_error(300) == "fetch_error"  # Non-standard

    def test_content_type_detection(self):
        """Test content type detection for text content."""
        assert self.fetcher._is_text_content_type("text/plain") is True
        assert self.fetcher._is_text_content_type("text/html") is True
        assert self.fetcher._is_text_content_type("application/javascript") is True
        assert self.fetcher._is_text_content_type("application/x-javascript") is True
        assert self.fetcher._is_text_content_type("application/json") is True
        assert self.fetcher._is_text_content_type("application/ecmascript") is True
        
        assert self.fetcher._is_text_content_type("image/png") is False
        assert self.fetcher._is_text_content_type("application/octet-stream") is False
        assert self.fetcher._is_text_content_type("video/mp4") is False
        
        assert self.fetcher._is_text_content_type("") is True  # Default to text
        assert self.fetcher._is_text_content_type(None) is True  # Default to text

    def test_binary_content_detection(self):
        """Test binary content detection algorithm."""
        # Text content
        assert self.fetcher._looks_like_binary("console.log('hello');") is False
        assert self.fetcher._looks_like_binary("var x = 1;\nfunction test() {}") is False
        assert self.fetcher._looks_like_binary("") is False
        
        # Binary-like content (high ratio of non-printable chars)
        binary_content = "".join(chr(i) for i in range(0, 20)) * 50  # Lots of control chars
        assert self.fetcher._looks_like_binary(binary_content) is True
        
        # Mixed content (some non-printable but mostly text - keep under 10% binary ratio)
        mixed_content = "console.log('test'); var x = 'hello world';" + chr(0) + chr(1)  # 2/45 chars = ~4.5%
        assert self.fetcher._looks_like_binary(mixed_content) is False

    def test_retry_decision_logic(self):
        """Test the retry decision logic."""
        fetcher_with_retry = RobustHttpFetcher()
        fetcher_without_retry = RobustHttpFetcher()
        fetcher_without_retry.retry_enabled = False
        
        # Should retry cases
        result_5xx = FetchResult(success=False, status_code=503, error_type="fetch_5xx")
        result_429 = FetchResult(success=False, status_code=429, error_type="fetch_4xx")
        result_timeout = FetchResult(success=False, error_type="fetch_timeout")
        result_network = FetchResult(success=False, error_type="network_error")
        
        assert fetcher_with_retry._should_retry(result_5xx) is True
        assert fetcher_with_retry._should_retry(result_429) is True
        assert fetcher_with_retry._should_retry(result_timeout) is True
        assert fetcher_with_retry._should_retry(result_network) is True
        
        # Should not retry cases
        result_404 = FetchResult(success=False, status_code=404, error_type="fetch_4xx")
        result_binary = FetchResult(success=False, error_type="binary_content")
        result_invalid = FetchResult(success=False, error_type="invalid_url")
        
        assert fetcher_with_retry._should_retry(result_404) is False
        assert fetcher_with_retry._should_retry(result_binary) is False
        assert fetcher_with_retry._should_retry(result_invalid) is False
        
        # Retry disabled
        assert fetcher_without_retry._should_retry(result_5xx) is False
        assert fetcher_without_retry._should_retry(result_429) is False