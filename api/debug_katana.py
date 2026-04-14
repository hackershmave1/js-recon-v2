#!/usr/bin/env python3
"""
Debug script to test Katana discovery and file processing.
"""
import asyncio
import sys
import os
from pathlib import Path

# Add app directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "app"))

from app.services.recon_job_runner import ReconJobRunner, ReconJobOptions
from app.db import get_db
from app.models import Session as DbSession, File as DbFile

async def test_katana_discovery():
    """Test Katana discovery pipeline"""
    print("🔍 Testing Katana discovery...")
    
    # Set up test environment
    os.environ['TESTING'] = 'true'
    os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
    os.environ['STORAGE_PATH'] = '/tmp/js-extractor-test'
    
    # Test Katana binary
    import shutil
    katana_binary = shutil.which("katana")
    print(f"📍 Katana binary: {katana_binary}")
    
    if not katana_binary:
        print("❌ Katana not found!")
        return
    
    # Test direct Katana command
    print("🧪 Testing direct Katana command...")
    import subprocess
    try:
        result = await asyncio.create_subprocess_exec(
            katana_binary, "-u", "https://httpbin.org", "-d", "1", 
            "-silent", "-j", "-timeout", "10", "-ct", "15s", "-jc", "-em", "js",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(result.communicate(), timeout=30)
        
        stdout_text = stdout.decode('utf-8', errors='ignore') if stdout else ""
        stderr_text = stderr.decode('utf-8', errors='ignore') if stderr else ""
        
        print(f"📤 Return code: {result.returncode}")
        print(f"📤 Stdout lines: {len(stdout_text.splitlines())}")
        print(f"📤 Stderr: {stderr_text[:200]}...")
        
        if stdout_text:
            print("📝 First few lines of output:")
            for i, line in enumerate(stdout_text.splitlines()[:3]):
                print(f"  {i+1}: {line[:100]}...")
        
    except Exception as e:
        print(f"❌ Direct Katana test failed: {e}")
        return
    
    # Test ReconJobRunner integration
    print("\n🔧 Testing ReconJobRunner integration...")
    
    try:
        # Create test session
        session_id = "test-katana-session"
        options = ReconJobOptions(
            session_id=session_id,
            discovery_engine="katana",
            max_depth=1,
            max_assets=10,
            timeout_seconds=10,
            include_sourcemaps=False,
            perform_analysis=False
        )
        
        # Get database connection
        db = next(get_db())
        
        # Create test session in database
        from uuid import UUID
        session_uuid = UUID("12345678-1234-1234-1234-123456789abc")
        db_session = DbSession(
            id=session_uuid,
            name="Test Katana Session",
            source="test_katana",
            version="3.0.0"
        )
        db.add(db_session)
        db.commit()
        
        runner = ReconJobRunner(session_id, options, db)
        
        # Test discovery
        print("🎯 Testing _discover_with_katana...")
        discovered_urls = await runner._discover_with_katana("https://httpbin.org")
        print(f"📊 Discovered {len(discovered_urls)} URLs:")
        for i, url in enumerate(list(discovered_urls)[:5]):
            print(f"  {i+1}: {url}")
        
        # Test full run
        print("\n🚀 Testing full recon run...")
        result = await runner.run(["https://httpbin.org"])
        
        print(f"📊 Recon results:")
        print(f"  Assets found: {len(result.get('assets', []))}")
        print(f"  Files stored: {result.get('ingestion', {}).get('stored', 0)}")
        print(f"  Cancelled: {result.get('cancelled', False)}")
        
        # Check database for files
        files = db.query(DbFile).filter(DbFile.session_id == session_uuid).all()
        print(f"📁 Files in database: {len(files)}")
        
        for i, file in enumerate(files[:3]):
            print(f"  {i+1}: {file.original_url} ({file.content_type})")
            
    except Exception as e:
        print(f"❌ ReconJobRunner test failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    asyncio.run(test_katana_discovery())