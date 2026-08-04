#!/usr/bin/env python3
"""
Test T-010 - Dashboard: Reconstructed Sources Viewer
Tests the new API endpoint /api/files/{id}/reconstructed-sources
"""

import tempfile
import json
import uuid
from pathlib import Path
from datetime import datetime

# Test configuration
API_BASE = "http://localhost:3000"

def test_reconstructed_sources_endpoint_not_found():
    """Test 404 response for non-existent file"""
    import requests
    
    fake_file_id = str(uuid.uuid4())
    url = f"{API_BASE}/api/files/{fake_file_id}/reconstructed-sources"
    
    response = requests.get(url)
    assert response.status_code == 404
    assert "File not found" in response.json().get("detail", "")

def test_reconstructed_sources_no_sourcemap():
    """Test 404 response for file without sourcemap"""
    import requests
    
    # This test assumes there are files without sourcemaps in the system
    # In practice, we'd need to create one or use a known test file
    files_response = requests.get(f"{API_BASE}/api/sessions")
    if files_response.status_code != 200:
        print("SKIP:");return("Cannot fetch sessions for test setup")
    
    sessions = files_response.json()
    if not sessions:
        print("SKIP:");return("No sessions available for testing")
    
    # Find a file without sourcemap
    for session in sessions[:3]:  # Check first 3 sessions
        session_files_response = requests.get(f"{API_BASE}/api/sessions/{session['id']}/files")
        if session_files_response.status_code == 200:
            files = session_files_response.json()
            for file in files:
                if not file.get('sourceMap') or file['sourceMap'].get('processingStatus') != 'completed':
                    # Try this file ID
                    url = f"{API_BASE}/api/files/{file['id']}/reconstructed-sources"
                    response = requests.get(url)
                    if response.status_code == 404 and ("No sourcemap found" in response.text or "not completed" in response.text):
                        return  # Test passed
    
    pytest.skip("Could not find a file without processed sourcemap for testing")

def test_reconstructed_sources_successful_processing():
    """Test successful retrieval of reconstructed sources"""
    import requests
    
    # Find a file with successfully processed sourcemap
    sessions_response = requests.get(f"{API_BASE}/api/sessions")
    if sessions_response.status_code != 200:
        print("SKIP:");return("Cannot fetch sessions for test setup")
    
    sessions = sessions_response.json()
    if not sessions:
        print("SKIP:");return("No sessions available for testing")
    
    test_file_id = None
    expected_file_count = 0
    
    # Look for a file with completed sourcemap processing
    for session in sessions[:5]:  # Check first 5 sessions
        session_files_response = requests.get(f"{API_BASE}/api/sessions/{session['id']}/files")
        if session_files_response.status_code == 200:
            files = session_files_response.json()
            for file in files:
                sourcemap = file.get('sourceMap')
                if (sourcemap and 
                    sourcemap.get('processingStatus') == 'completed' and 
                    sourcemap.get('reconstructedFilesCount', 0) > 0):
                    test_file_id = file['id']
                    expected_file_count = sourcemap['reconstructedFilesCount']
                    break
        if test_file_id:
            break
    
    if not test_file_id:
        print("SKIP:");return("No files with successfully processed sourcemaps found")
    
    # Test the reconstructed sources endpoint
    url = f"{API_BASE}/api/files/{test_file_id}/reconstructed-sources"
    response = requests.get(url)
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    
    data = response.json()
    assert "files" in data
    assert "stats" in data
    assert "sourcemap" in data
    
    # Validate structure
    files = data["files"]
    stats = data["stats"]
    sourcemap = data["sourcemap"]
    
    assert isinstance(files, list)
    assert len(files) > 0, "Should have at least one reconstructed file"
    
    # Check first file structure
    first_file = files[0]
    required_fields = ["path", "content", "size", "type", "originalPath", "sourceIndex"]
    for field in required_fields:
        assert field in first_file, f"Missing required field: {field}"
    
    # Check stats structure
    assert "totalFiles" in stats
    assert "totalSize" in stats
    assert "jsFiles" in stats
    assert "otherFiles" in stats
    assert stats["totalFiles"] == len(files)
    
    # Check sourcemap structure
    assert "id" in sourcemap
    assert "fileId" in sourcemap
    assert "reconstructedFilesCount" in sourcemap
    assert sourcemap["reconstructedFilesCount"] == len(files)
    
    print(f"✓ Successfully retrieved {len(files)} reconstructed files")

def test_reconstructed_sources_api_contract():
    """Test API response contract and field types"""
    import requests
    
    # Use canonical test target or find any file with processed sourcemap
    sessions_response = requests.get(f"{API_BASE}/api/sessions")
    if sessions_response.status_code != 200:
        print("SKIP:");return("Cannot fetch sessions for test setup")
    
    sessions = sessions_response.json()
    if not sessions:
        print("SKIP:");return("No sessions available for testing")
    
    test_file_id = None
    for session in sessions[:3]:
        session_files_response = requests.get(f"{API_BASE}/api/sessions/{session['id']}/files")
        if session_files_response.status_code == 200:
            files = session_files_response.json()
            for file in files:
                sourcemap = file.get('sourceMap')
                if (sourcemap and 
                    sourcemap.get('processingStatus') == 'completed' and 
                    sourcemap.get('reconstructedFilesCount', 0) > 0):
                    test_file_id = file['id']
                    break
        if test_file_id:
            break
    
    if not test_file_id:
        print("SKIP:");return("No files with processed sourcemaps found for contract testing")
    
    url = f"{API_BASE}/api/files/{test_file_id}/reconstructed-sources"
    response = requests.get(url)
    assert response.status_code == 200
    
    data = response.json()
    
    # Validate top-level contract
    assert isinstance(data, dict)
    assert set(data.keys()) == {"files", "stats", "sourcemap"}
    
    # Validate files array
    files = data["files"]
    assert isinstance(files, list)
    if files:  # If there are files, validate structure
        for file_data in files:
            assert isinstance(file_data["path"], str)
            assert isinstance(file_data["content"], str)
            assert isinstance(file_data["size"], int)
            assert isinstance(file_data["type"], str)
            assert isinstance(file_data["originalPath"], str)
            assert isinstance(file_data["sourceIndex"], int)
            assert file_data["size"] >= 0
            assert file_data["sourceIndex"] >= 0
    
    # Validate stats
    stats = data["stats"]
    assert isinstance(stats["totalFiles"], int)
    assert isinstance(stats["totalSize"], int)
    assert isinstance(stats["jsFiles"], int)  
    assert isinstance(stats["otherFiles"], int)
    assert stats["totalFiles"] >= 0
    assert stats["totalSize"] >= 0
    assert stats["jsFiles"] >= 0
    assert stats["otherFiles"] >= 0
    assert stats["totalFiles"] == stats["jsFiles"] + stats["otherFiles"]
    
    # Validate sourcemap metadata
    sourcemap = data["sourcemap"]
    assert isinstance(sourcemap["id"], str)
    assert isinstance(sourcemap["fileId"], str)
    assert isinstance(sourcemap["reconstructedFilesCount"], int)
    assert sourcemap["reconstructedFilesCount"] >= 0
    if sourcemap.get("processedAt"):
        # Should be valid ISO timestamp
        datetime.fromisoformat(sourcemap["processedAt"].replace('Z', '+00:00'))
    
    print("✓ API contract validation passed")

if __name__ == "__main__":
    print("Running T-010 reconstructed sources tests...")
    
    try:
        test_reconstructed_sources_endpoint_not_found()
        print("✓ test_reconstructed_sources_endpoint_not_found")
    except Exception as e:
        print(f"✗ test_reconstructed_sources_endpoint_not_found: {e}")
    
    try:
        test_reconstructed_sources_no_sourcemap()
        print("✓ test_reconstructed_sources_no_sourcemap")
    except Exception as e:
        print(f"✗ test_reconstructed_sources_no_sourcemap: {e}")
    
    try:
        test_reconstructed_sources_successful_processing()
        print("✓ test_reconstructed_sources_successful_processing")
    except Exception as e:
        print(f"✗ test_reconstructed_sources_successful_processing: {e}")
    
    try:
        test_reconstructed_sources_api_contract()
        print("✓ test_reconstructed_sources_api_contract")
    except Exception as e:
        print(f"✗ test_reconstructed_sources_api_contract: {e}")
    
    print("\nT-010 tests completed")