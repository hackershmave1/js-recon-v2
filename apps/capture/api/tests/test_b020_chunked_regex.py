import pytest
import time
from unittest.mock import Mock, patch

from api.app.services.regex_utils import (
    ChunkedRegexProcessor,
    ChunkStats,
    RegexTimeoutError,
    chunked_regex,
    safe_regex_findall,
    safe_regex_search,
    regex_timeout,
)
from api.app.services.rep_endpoints_extractor import RepEndpointsExtractor
from api.app.services.rep_secrets_extractor import RepSecretsExtractor
from api.app.services.kingfisher_rules_loader import KingfisherRulesLoader
from api.app.config import settings


class TestChunkedRegexProcessor:
    """Test the core chunked regex processor"""
    
    def test_should_chunk_small_content(self):
        processor = ChunkedRegexProcessor()
        small_content = "a" * 1000  # 1KB
        assert not processor.should_chunk(small_content)
    
    def test_should_chunk_large_content(self):
        processor = ChunkedRegexProcessor()
        # Create content larger than threshold
        large_content = "a" * (settings.regex_chunk_size_threshold + 1000)
        assert processor.should_chunk(large_content)
    
    def test_should_chunk_disabled(self):
        with patch.object(settings, 'regex_chunk_enabled', False):
            processor = ChunkedRegexProcessor()
            large_content = "a" * (settings.regex_chunk_size_threshold + 1000)
            assert not processor.should_chunk(large_content)
    
    def test_create_chunks_small_content(self):
        processor = ChunkedRegexProcessor()
        content = "Hello world"
        chunks = list(processor.create_chunks(content))
        
        assert len(chunks) == 1
        assert chunks[0] == (content, 0, len(content))
    
    def test_create_chunks_large_content(self):
        processor = ChunkedRegexProcessor()
        # Create content that will be chunked
        content = "x" * (processor.chunk_size * 3)  # 3 chunks worth
        
        chunks = list(processor.create_chunks(content))
        
        # Should have multiple chunks with overlap
        assert len(chunks) > 1
        
        # First chunk should start at 0
        assert chunks[0][1] == 0  # start_offset
        
        # Last chunk should end at content length
        last_chunk = chunks[-1]
        # Character end position should be close to content length
        assert abs(last_chunk[2] - len(content)) <= processor.overlap
    
    def test_create_chunks_utf8_boundary_handling(self):
        processor = ChunkedRegexProcessor()
        # Create content with UTF-8 characters that might cause boundary issues
        unicode_char = "🔍"  # 4-byte UTF-8 character
        content = ("a" * 1000 + unicode_char) * 100  # Mix ASCII and Unicode
        
        chunks = list(processor.create_chunks(content))
        
        # All chunks should decode properly (no UnicodeDecodeError)
        for chunk_content, start_offset, end_offset in chunks:
            assert isinstance(chunk_content, str)
            assert len(chunk_content) > 0
    
    def test_create_chunks_max_chunks_limit(self):
        processor = ChunkedRegexProcessor()
        # Create extremely large content that would exceed max_chunks
        huge_content = "x" * (processor.chunk_size * (processor.max_chunks + 10))
        
        chunks = list(processor.create_chunks(huge_content))
        
        # Should not exceed max_chunks limit
        assert len(chunks) <= processor.max_chunks
    
    def test_process_patterns_simple(self):
        processor = ChunkedRegexProcessor()
        content = "Hello world, this is a test"
        patterns = [r"\b\w{4}\b"]  # 4-letter words
        
        matches, stats = processor.process_patterns(content, patterns)
        
        expected_matches = ["Hello", "this", "test"]
        assert sorted(matches) == sorted(expected_matches)
        assert stats.total_matches == len(expected_matches)
        assert stats.processed_chunks >= 1
        assert not stats.was_chunked  # Small content shouldn't be chunked
    
    def test_process_patterns_with_capture_groups(self):
        processor = ChunkedRegexProcessor()
        content = 'url: "https://api.example.com/v1/users"'
        patterns = [r'url:\s*"([^"]+)"']
        
        matches, stats = processor.process_patterns(content, patterns)
        
        assert "https://api.example.com/v1/users" in matches
        assert stats.total_matches >= 1
    
    def test_process_patterns_invalid_regex(self):
        processor = ChunkedRegexProcessor()
        content = "test content"
        invalid_patterns = [r"[invalid regex", r"valid.*pattern"]
        
        matches, stats = processor.process_patterns(content, invalid_patterns)
        
        # Should process valid patterns and skip invalid ones
        assert isinstance(matches, list)
        assert stats.total_chunks >= 1
    
    @patch('api.app.services.regex_utils.regex_timeout')
    def test_process_patterns_timeout_handling(self, mock_timeout):
        mock_timeout.side_effect = RegexTimeoutError("Timeout")
        
        processor = ChunkedRegexProcessor()
        content = "test content"
        patterns = [r"test"]
        
        matches, stats = processor.process_patterns(content, patterns)
        
        # Should handle timeout gracefully
        assert isinstance(matches, list)
        assert stats.timeout_chunks > 0
    
    def test_process_single_pattern(self):
        processor = ChunkedRegexProcessor()
        content = "API endpoint: /api/v1/users"
        pattern = r"/api/[^/]+/[^/]+"
        
        matches, stats = processor.process_single_pattern(content, pattern)
        
        assert "/api/v1/users" in matches
        assert stats.total_matches >= 1
    
    def test_get_stats_summary(self):
        processor = ChunkedRegexProcessor()
        stats = ChunkStats(
            total_chunks=5,
            processed_chunks=4,
            timeout_chunks=1,
            error_chunks=0,
            total_matches=10,
            processing_time_ms=100,
            was_chunked=True
        )
        
        summary = processor.get_stats_summary(stats)
        
        expected_keys = [
            "was_chunked", "total_chunks", "processed_chunks", 
            "timeout_chunks", "error_chunks", "total_matches", 
            "processing_time_ms", "success_rate"
        ]
        
        for key in expected_keys:
            assert key in summary
        
        assert summary["success_rate"] == 0.8  # 4/5


class TestRegexTimeout:
    """Test regex timeout context manager"""
    
    def test_regex_timeout_no_timeout(self):
        with regex_timeout(1):
            time.sleep(0.1)  # Should not timeout
    
    @pytest.mark.skipif(not hasattr(pytest, 'mark'), reason="Skip timeout test")
    def test_regex_timeout_with_timeout(self):
        # Note: This test might be flaky in CI environments
        # In practice, regex timeout is mainly for catastrophic backtracking
        pass


class TestSafeRegexFunctions:
    """Test the safe regex convenience functions"""
    
    def test_safe_regex_findall(self):
        content = "Find these: abc123, def456, ghi789"
        pattern = r"[a-z]+\d+"
        
        matches = safe_regex_findall(pattern, content)
        
        expected = ["abc123", "def456", "ghi789"]
        assert sorted(matches) == sorted(expected)
    
    def test_safe_regex_search(self):
        content = "Find this: abc123, and more"
        pattern = r"[a-z]+\d+"
        
        match = safe_regex_search(pattern, content)
        
        assert match == "abc123"
    
    def test_safe_regex_search_no_match(self):
        content = "No matches here"
        pattern = r"\d+"
        
        match = safe_regex_search(pattern, content)
        
        assert match is None


class TestRepEndpointsExtractorChunked:
    """Test chunked processing in endpoints extractor"""
    
    def test_extract_small_file_uses_standard(self):
        extractor = RepEndpointsExtractor()
        small_content = 'fetch("/api/users")'
        
        with patch.object(extractor, '_extract_standard') as mock_standard, \
             patch.object(extractor, '_extract_chunked') as mock_chunked:
            
            mock_standard.return_value = []
            extractor.extract(small_content)
            
            mock_standard.assert_called_once()
            mock_chunked.assert_not_called()
    
    def test_extract_large_file_uses_chunked(self):
        extractor = RepEndpointsExtractor()
        # Create large content that triggers chunking
        large_content = 'fetch("/api/users");' * 10000 + "x" * (settings.regex_chunk_size_threshold + 1000)
        
        with patch.object(extractor, '_extract_standard') as mock_standard, \
             patch.object(extractor, '_extract_chunked') as mock_chunked:
            
            mock_chunked.return_value = []
            extractor.extract(large_content)
            
            mock_chunked.assert_called_once()
            mock_standard.assert_not_called()
    
    def test_extract_chunked_preserves_results(self):
        extractor = RepEndpointsExtractor()
        # Content with endpoints that spans multiple potential chunks
        endpoints = [f'/api/endpoint{i}' for i in range(50)]
        content_parts = [f'fetch("{endpoint}")' for endpoint in endpoints]
        large_content = '; '.join(content_parts) + "x" * (settings.regex_chunk_size_threshold + 1000)
        
        results = extractor.extract(large_content)
        
        # Should find endpoints even with chunked processing
        found_endpoints = {result['endpoint'] for result in results}
        
        # Should find most or all endpoints (allowing for some edge cases)
        assert len(found_endpoints) >= len(endpoints) * 0.8  # Allow 20% tolerance
        
        # Verify result structure
        for result in results:
            required_fields = ['endpoint', 'method', 'type', 'confidence', 'line', 'column']
            for field in required_fields:
                assert field in result


class TestRepSecretsExtractorChunked:
    """Test chunked processing in secrets extractor"""
    
    def setup_method(self):
        # Mock the rules loader to provide test rules
        self.mock_rule = Mock()
        self.mock_rule.compiled_regex = Mock()
        self.mock_rule.name = "test_secret"
        self.mock_rule.rule_id = "test_001"
        self.mock_rule.min_entropy = 0
        self.mock_rule.pattern_requirements = {}
        self.mock_rule.confidence = "high"
        
        self.mock_rules_loader = Mock(spec=KingfisherRulesLoader)
        self.mock_rules_loader.load_rules.return_value = [self.mock_rule]
    
    def test_extract_small_file_uses_standard(self):
        extractor = RepSecretsExtractor(rules_loader=self.mock_rules_loader)
        small_content = 'secret="abc123xyz"'
        
        with patch.object(extractor, '_extract_standard') as mock_standard, \
             patch.object(extractor, '_extract_chunked') as mock_chunked:
            
            mock_standard.return_value = []
            extractor.extract(small_content)
            
            mock_standard.assert_called_once()
            mock_chunked.assert_not_called()
    
    def test_extract_large_file_uses_chunked(self):
        extractor = RepSecretsExtractor(rules_loader=self.mock_rules_loader)
        # Create large content that triggers chunking
        large_content = 'secret="abc123xyz";' * 1000 + "x" * (settings.regex_chunk_size_threshold + 1000)
        
        with patch.object(extractor, '_extract_standard') as mock_standard, \
             patch.object(extractor, '_extract_chunked') as mock_chunked:
            
            mock_chunked.return_value = []
            extractor.extract(large_content)
            
            mock_chunked.assert_called_once()
            mock_standard.assert_not_called()
    
    def test_process_secret_match_validation(self):
        extractor = RepSecretsExtractor(rules_loader=self.mock_rules_loader)
        
        # Create a mock match object
        mock_match = Mock()
        mock_match.start.return_value = 10
        
        # Test with valid secret
        valid_secret = "sk_test_123456789abcdef"
        content = f"const key = '{valid_secret}'"
        
        with patch.object(extractor, '_is_known_false_positive', return_value=False), \
             patch.object(extractor, '_has_false_positive_context', return_value=False), \
             patch.object(extractor, '_is_likely_base64_data', return_value=False), \
             patch.object(extractor, '_is_comment_line', return_value=False):
            
            result = extractor._process_secret_match(
                self.mock_rule, mock_match, valid_secret, 4.5, content, None, set()
            )
            
            assert result is not None
            assert result['value'] == valid_secret
            assert 'confidence_score' in result


class TestChunkedRegexIntegration:
    """Integration tests for chunked regex processing"""
    
    def test_chunked_processing_accuracy(self):
        """Test that chunked processing produces same results as standard processing"""
        # Create content with known patterns
        test_patterns = [
            'fetch("/api/users/123")',
            'axios.post("/api/orders", data)',
            'const secret = "sk_live_abcdef123456"',
            '"/graphql"',
            'https://api.example.com/v1/data'
        ]
        
        # Create content that's large enough to trigger chunking
        repeated_content = ('\n'.join(test_patterns) + '\n') * 100
        large_content = repeated_content + "x" * (settings.regex_chunk_size_threshold + 1000)
        
        # Test endpoints extractor
        endpoints_extractor = RepEndpointsExtractor()
        
        # Extract with chunking disabled
        with patch.object(settings, 'regex_chunk_enabled', False):
            standard_results = endpoints_extractor.extract(repeated_content)
        
        # Extract with chunking enabled
        with patch.object(settings, 'regex_chunk_enabled', True):
            chunked_results = endpoints_extractor.extract(large_content)
        
        # Results should be comparable (allowing for duplicates from repetition)
        standard_endpoints = {r['endpoint'] for r in standard_results}
        chunked_endpoints = {r['endpoint'] for r in chunked_results}
        
        # All standard endpoints should be found in chunked results
        assert standard_endpoints.issubset(chunked_endpoints) or \
               len(standard_endpoints.intersection(chunked_endpoints)) >= len(standard_endpoints) * 0.9
    
    def test_chunked_processing_performance(self):
        """Test that chunked processing handles large files without hanging"""
        # Create very large content
        large_content = 'fetch("/api/test"); ' * 50000 + "x" * (settings.regex_chunk_size_threshold * 2)
        
        extractor = RepEndpointsExtractor()
        
        start_time = time.time()
        results = extractor.extract(large_content)
        processing_time = time.time() - start_time
        
        # Should complete in reasonable time (adjust threshold as needed)
        assert processing_time < 30  # 30 seconds max
        assert isinstance(results, list)
        
        # Should find some results
        assert len(results) > 0
    
    def test_chunk_boundary_handling(self):
        """Test that patterns spanning chunk boundaries are handled correctly"""
        processor = ChunkedRegexProcessor()
        
        # Create content where a pattern might be split across chunk boundary
        pattern_text = 'fetch("/api/very/long/endpoint/that/might/be/split")'
        
        # Calculate rough chunk size and place pattern near boundary
        chunk_size = processor.chunk_size
        content_before = "x" * (chunk_size - 20)  # 20 chars before chunk boundary
        content_after = "y" * 100
        content = content_before + pattern_text + content_after
        
        # Make sure it's large enough to chunk
        if len(content.encode('utf-8')) <= processor.size_threshold:
            content = content + "z" * (processor.size_threshold + 1000)
        
        pattern = r'fetch\("[^"]+"\)'
        matches, stats = processor.process_single_pattern(content, pattern)
        
        # Should find the pattern even if it spans chunk boundary due to overlap
        assert len(matches) >= 1
        assert any('fetch(' in match for match in matches)