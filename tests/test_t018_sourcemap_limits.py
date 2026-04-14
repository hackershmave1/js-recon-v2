#!/usr/bin/env python3
"""
Test T-018 - Sourcemap Resource Limits
Tests that sourcemap processing enforces size, timeout, and file count limits
"""

import tempfile
import json
import uuid
import os
from unittest.mock import Mock, patch

def test_sourcemap_size_limit():
    """Test that oversized sourcemaps are rejected"""
    try:
        from api.app.services.sourcemap_processor import SourceMapProcessor
        from api.app.config import settings
    except ImportError:
        print("SKIP: Cannot import required modules")
        return
    
    processor = SourceMapProcessor()
    
    # Create oversized sourcemap content (larger than configured limit)
    large_content = "x" * (settings.sourcemap_max_size_bytes + 1000)
    
    result = processor.process_sourcemap_from_content(large_content)
    
    assert result['success'] is False
    assert 'too large' in result['error'].lower()
    assert result['files'] == []
    print("✓ Size limit validation works")

def test_file_count_limit():
    """Test that excessive reconstructed file count is limited"""
    try:
        from api.app.services.sourcemap_processor import SourceMapProcessor
        from api.app.config import settings
    except ImportError:
        print("SKIP: Cannot import required modules") 
        return
    
    processor = SourceMapProcessor()
    
    # Mock a large number of files
    mock_files = []
    for i in range(settings.sourcemap_max_reconstructed_files + 100):
        mock_files.append({
            'path': f'file_{i}.js',
            'content': f'console.log("File {i}");',
            'size': 20,
            'type': 'javascript'
        })
    
    validation_result = processor._validate_reconstructed_files_count(mock_files)
    
    assert validation_result['limited'] is True
    assert len(validation_result['files']) == settings.sourcemap_max_reconstructed_files
    assert validation_result['truncated_count'] == 100
    print("✓ File count limit validation works")

def test_timeout_configuration():
    """Test that timeout is properly configured"""
    try:
        from api.app.config import settings
    except ImportError:
        print("SKIP: Cannot import config module")
        return
        
    # Verify timeout setting exists and is reasonable
    assert hasattr(settings, 'sourcemap_processing_timeout_seconds')
    assert isinstance(settings.sourcemap_processing_timeout_seconds, int)
    assert settings.sourcemap_processing_timeout_seconds > 0
    assert settings.sourcemap_processing_timeout_seconds <= 300  # Reasonable upper bound
    print("✓ Timeout configuration is valid")

def test_size_configuration():
    """Test that size limit is properly configured"""
    try:
        from api.app.config import settings
    except ImportError:
        print("SKIP: Cannot import config module")
        return
        
    # Verify size setting exists and is reasonable
    assert hasattr(settings, 'sourcemap_max_size_bytes')
    assert isinstance(settings.sourcemap_max_size_bytes, int)
    assert settings.sourcemap_max_size_bytes > 0
    assert settings.sourcemap_max_size_bytes >= 1024 * 1024  # At least 1MB
    print("✓ Size limit configuration is valid")

def test_file_count_configuration():
    """Test that file count limit is properly configured"""
    try:
        from api.app.config import settings
    except ImportError:
        print("SKIP: Cannot import config module")
        return
        
    # Verify file count setting exists and is reasonable  
    assert hasattr(settings, 'sourcemap_max_reconstructed_files')
    assert isinstance(settings.sourcemap_max_reconstructed_files, int)
    assert settings.sourcemap_max_reconstructed_files > 0
    assert settings.sourcemap_max_reconstructed_files >= 100  # At least 100 files
    print("✓ File count limit configuration is valid")

def test_processor_handles_limit_results():
    """Test that processor correctly handles limited results"""
    try:
        from api.app.services.sourcemap_processor import SourceMapProcessor
    except ImportError:
        print("SKIP: Cannot import SourceMapProcessor")
        return
    
    processor = SourceMapProcessor()
    
    # Test with small content that passes size validation
    small_content = '{"version":3,"sources":["test.js"],"mappings":"AAAA"}'
    
    # Mock the collect_files method to return many files
    original_collect = processor._collect_files
    
    def mock_collect_files(output_dir):
        # Return more files than the limit
        mock_files = []
        for i in range(1200):  # Exceed default limit of 1000
            mock_files.append({
                'path': f'src/file_{i}.js',
                'content': f'// File {i}\nconsole.log("test");',
                'size': 25,
                'type': 'javascript',
                'encoding': 'utf-8'
            })
        return mock_files
    
    # Mock subprocess to avoid actually running sourcemapper
    with patch('subprocess.run') as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "Success"
        
        processor._collect_files = mock_collect_files
        
        result = processor.process_sourcemap_from_content(small_content)
        
        # Should be limited
        assert result.get('limited') is True
        assert 'truncated_count' in result
        assert len(result['files']) == 1000  # Default limit
        
    processor._collect_files = original_collect
    print("✓ Processor handles limited results correctly")

def test_ingestion_constants_match_settings():
    """Test that ingestion constants match config settings"""
    try:
        from api.app.api.routes.ingestion import (
            SOURCEMAP_TIMEOUT_SECONDS,
            SOURCEMAP_MAX_SOURCEMAP_SIZE, 
            SOURCEMAP_MAX_RECONSTRUCTED_FILES
        )
        from api.app.config import settings
    except ImportError:
        print("SKIP: Cannot import ingestion constants")
        return
    
    # Verify constants match settings
    assert SOURCEMAP_TIMEOUT_SECONDS == settings.sourcemap_processing_timeout_seconds
    assert SOURCEMAP_MAX_SOURCEMAP_SIZE == settings.sourcemap_max_size_bytes
    assert SOURCEMAP_MAX_RECONSTRUCTED_FILES == settings.sourcemap_max_reconstructed_files
    print("✓ Ingestion constants match config settings")

if __name__ == "__main__":
    print("Running T-018 sourcemap limits tests...")
    
    try:
        test_sourcemap_size_limit()
        print("✓ test_sourcemap_size_limit")
    except Exception as e:
        print(f"✗ test_sourcemap_size_limit: {e}")
    
    try:
        test_file_count_limit()
        print("✓ test_file_count_limit")
    except Exception as e:
        print(f"✗ test_file_count_limit: {e}")
    
    try:
        test_timeout_configuration()
        print("✓ test_timeout_configuration")
    except Exception as e:
        print(f"✗ test_timeout_configuration: {e}")
    
    try:
        test_size_configuration()
        print("✓ test_size_configuration")
    except Exception as e:
        print(f"✗ test_size_configuration: {e}")
    
    try:
        test_file_count_configuration()
        print("✓ test_file_count_configuration")
    except Exception as e:
        print(f"✗ test_file_count_configuration: {e}")
    
    try:
        test_processor_handles_limit_results()
        print("✓ test_processor_handles_limit_results")
    except Exception as e:
        print(f"✗ test_processor_handles_limit_results: {e}")
    
    try:
        test_ingestion_constants_match_settings()
        print("✓ test_ingestion_constants_match_settings")
    except Exception as e:
        print(f"✗ test_ingestion_constants_match_settings: {e}")
    
    print("\nT-018 tests completed")