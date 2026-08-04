import re
import signal
import logging
from typing import List, Dict, Any, Set, Iterator, Tuple, Optional
from contextlib import contextmanager
from dataclasses import dataclass

from ..config import settings

logger = logging.getLogger(__name__)


@dataclass
class ChunkStats:
    """Statistics for chunked regex processing"""
    total_chunks: int = 0
    processed_chunks: int = 0
    timeout_chunks: int = 0
    error_chunks: int = 0
    total_matches: int = 0
    processing_time_ms: int = 0
    was_chunked: bool = False


class RegexTimeoutError(Exception):
    """Raised when regex processing times out"""
    pass


@contextmanager
def regex_timeout(seconds: int):
    """Context manager to timeout regex operations"""
    def timeout_handler(signum, frame):
        raise RegexTimeoutError("Regex operation timed out")
    
    # Only use signal timeout in main thread
    try:
        if hasattr(signal, 'SIGALRM'):
            old_handler = signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(seconds)
            try:
                yield
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)
        else:
            # Fallback for systems without SIGALRM
            yield
    except Exception:
        # If signal handling fails, continue without timeout
        yield


class ChunkedRegexProcessor:
    """
    Safe regex processing that chunks large content to avoid worst-case latency
    """
    
    def __init__(self):
        self.chunk_enabled = settings.regex_chunk_enabled
        self.size_threshold = settings.regex_chunk_size_threshold
        self.chunk_size = settings.regex_chunk_size
        self.overlap = settings.regex_chunk_overlap
        self.timeout = settings.regex_chunk_timeout
        self.max_chunks = settings.regex_chunk_max_chunks
    
    def should_chunk(self, content: str) -> bool:
        """Determine if content should be processed in chunks"""
        if not self.chunk_enabled:
            return False
        return len(content.encode('utf-8')) > self.size_threshold
    
    def create_chunks(self, content: str) -> Iterator[Tuple[str, int, int]]:
        """
        Split content into overlapping chunks
        
        Yields:
            Tuple of (chunk_content, start_offset, end_offset)
        """
        if not self.should_chunk(content):
            yield (content, 0, len(content))
            return
        
        content_bytes = content.encode('utf-8')
        total_size = len(content_bytes)
        
        if total_size <= self.chunk_size:
            yield (content, 0, len(content))
            return
        
        # Calculate number of chunks and validate limits
        estimated_chunks = (total_size + self.chunk_size - 1) // self.chunk_size
        if estimated_chunks > self.max_chunks:
            logger.warning(f"Content would create {estimated_chunks} chunks, limiting to {self.max_chunks}")
            # Process only the first portion that fits within chunk limits
            max_content_size = self.max_chunks * self.chunk_size
            content_bytes = content_bytes[:max_content_size]
            total_size = len(content_bytes)
        
        start = 0
        chunk_num = 0
        
        while start < total_size and chunk_num < self.max_chunks:
            # Calculate chunk end position
            end = min(start + self.chunk_size, total_size)
            
            # Extract chunk bytes and decode safely
            chunk_bytes = content_bytes[start:end]
            
            # Handle UTF-8 boundary issues
            try:
                chunk_content = chunk_bytes.decode('utf-8')
            except UnicodeDecodeError:
                # If we hit a UTF-8 boundary, back up to a safe position
                safe_end = end
                while safe_end > start:
                    try:
                        chunk_content = content_bytes[start:safe_end].decode('utf-8')
                        break
                    except UnicodeDecodeError:
                        safe_end -= 1
                else:
                    # If we can't find a safe decode position, skip this chunk
                    logger.warning(f"Unable to safely decode chunk {chunk_num}, skipping")
                    start = end
                    chunk_num += 1
                    continue
                end = safe_end
            
            # Calculate character positions (approximation)
            char_start = len(content_bytes[:start].decode('utf-8', errors='ignore'))
            char_end = len(content_bytes[:end].decode('utf-8', errors='ignore'))
            
            yield (chunk_content, char_start, char_end)
            
            # Move start position for next chunk, accounting for overlap
            start = max(start + self.chunk_size - self.overlap, end)
            chunk_num += 1
    
    def process_patterns(self, content: str, patterns: List[str], flags: int = 0) -> Tuple[List[str], ChunkStats]:
        """
        Process regex patterns against content with chunking for large content
        
        Args:
            content: Text content to search
            patterns: List of regex patterns
            flags: Regex flags
            
        Returns:
            Tuple of (matches, stats)
        """
        import time
        start_time = time.time()
        
        stats = ChunkStats()
        stats.was_chunked = self.should_chunk(content)
        
        all_matches: Set[str] = set()
        
        # Compile patterns once
        compiled_patterns = []
        for pattern in patterns:
            try:
                compiled_patterns.append(re.compile(pattern, flags))
            except re.error as e:
                logger.warning(f"Invalid regex pattern '{pattern}': {e}")
                continue
        
        if not compiled_patterns:
            return [], stats
        
        # Process in chunks
        for chunk_content, start_offset, end_offset in self.create_chunks(content):
            stats.total_chunks += 1
            
            try:
                with regex_timeout(self.timeout):
                    chunk_matches = self._process_chunk_patterns(chunk_content, compiled_patterns)
                    all_matches.update(chunk_matches)
                    stats.processed_chunks += 1
                    
            except RegexTimeoutError:
                stats.timeout_chunks += 1
                logger.warning(f"Regex timeout on chunk {stats.processed_chunks + 1}")
                
            except Exception as e:
                stats.error_chunks += 1
                logger.warning(f"Regex error on chunk {stats.processed_chunks + 1}: {e}")
        
        stats.total_matches = len(all_matches)
        stats.processing_time_ms = int((time.time() - start_time) * 1000)
        
        return list(all_matches), stats
    
    def _process_chunk_patterns(self, chunk: str, compiled_patterns: List[re.Pattern]) -> Set[str]:
        """Process regex patterns against a single chunk"""
        matches: Set[str] = set()
        
        for pattern in compiled_patterns:
            try:
                chunk_matches = pattern.findall(chunk)
                if isinstance(chunk_matches[0], tuple) if chunk_matches else False:
                    # Handle patterns with capture groups
                    for match_tuple in chunk_matches:
                        if isinstance(match_tuple, tuple):
                            matches.update(str(m) for m in match_tuple if m)
                        else:
                            matches.add(str(match_tuple))
                else:
                    # Handle simple patterns
                    matches.update(str(m) for m in chunk_matches)
                    
            except Exception as e:
                logger.debug(f"Pattern {pattern.pattern} failed on chunk: {e}")
                continue
        
        return matches
    
    def process_single_pattern(self, content: str, pattern: str, flags: int = 0) -> Tuple[List[str], ChunkStats]:
        """
        Process a single regex pattern against content
        
        Args:
            content: Text content to search
            pattern: Regex pattern  
            flags: Regex flags
            
        Returns:
            Tuple of (matches, stats)
        """
        return self.process_patterns(content, [pattern], flags)
    
    def get_stats_summary(self, stats: ChunkStats) -> Dict[str, Any]:
        """Get a summary of chunked processing stats"""
        return {
            "was_chunked": stats.was_chunked,
            "total_chunks": stats.total_chunks,
            "processed_chunks": stats.processed_chunks,
            "timeout_chunks": stats.timeout_chunks,
            "error_chunks": stats.error_chunks,
            "total_matches": stats.total_matches,
            "processing_time_ms": stats.processing_time_ms,
            "success_rate": stats.processed_chunks / stats.total_chunks if stats.total_chunks > 0 else 0
        }


# Global instance
chunked_regex = ChunkedRegexProcessor()


def safe_regex_findall(pattern: str, content: str, flags: int = 0) -> List[str]:
    """
    Safe regex findall that uses chunking for large content
    
    Args:
        pattern: Regex pattern
        content: Content to search
        flags: Regex flags
        
    Returns:
        List of matches
    """
    matches, _ = chunked_regex.process_single_pattern(content, pattern, flags)
    return matches


def safe_regex_search(pattern: str, content: str, flags: int = 0) -> Optional[str]:
    """
    Safe regex search that returns first match using chunking if needed
    
    Args:
        pattern: Regex pattern
        content: Content to search  
        flags: Regex flags
        
    Returns:
        First match or None
    """
    matches, _ = chunked_regex.process_single_pattern(content, pattern, flags)
    return matches[0] if matches else None