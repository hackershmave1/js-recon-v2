#!/usr/bin/env python3
"""
Analyze wishandwash.co.il JavaScript file using our security extractor
"""

import requests
import sys
import json
import os

# Add the API app to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))

def fetch_js_content(url):
    """Fetch JavaScript content from URL"""
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.text
    except Exception as e:
        print(f"Error fetching JS content: {e}")
        return None

def analyze_with_extractor(content):
    """Analyze content using our ComprehensiveExtractor"""
    try:
        from app.services.comprehensive_extractor import ComprehensiveExtractor
        from app.services.analysis_triggers import SmartAnalysisTriggers
        
        extractor = ComprehensiveExtractor()
        triggers = SmartAnalysisTriggers()
        
        # Check if content meets smart trigger criteria
        trigger_result = triggers.should_trigger_analysis(
            content=content,
            file_metadata={"url": "https://wishandwash.co.il/assets/index-BDSyL5Fh.js"},
            manual_analysis_requested=True  # Force analysis
        )
        
        print(f"File size: {len(content)} bytes")
        print(f"Smart trigger criteria met: {trigger_result['criteria_met']}")
        print("=" * 60)
        
        # Run analysis
        metadata = {
            "url": "https://wishandwash.co.il/assets/index-BDSyL5Fh.js",
            "contentType": "application/javascript",
            "analysisTimestamp": "2026-02-09T21:15:00Z"
        }
        
        results = extractor.extract_all(content, metadata, options={
            "include_sourcemap": True,
            "resolve_urls": True
        })
        
        # Extract and display findings
        analysis = results.get("analysis", {})
        
        print("🔍 ENDPOINTS DISCOVERED:")
        endpoints = analysis.get("endpoints", [])
        if endpoints:
            for i, endpoint in enumerate(endpoints, 1):
                url = endpoint.get("url", "")
                method = endpoint.get("method", "GET")
                context = endpoint.get("context", "")
                print(f"  {i}. {method} {url}")
                if context:
                    print(f"     Context: {context[:100]}...")
        else:
            print("  No endpoints found")
        
        print("\n🔐 SECRETS/TOKENS DISCOVERED:")
        secrets = analysis.get("secrets", [])
        if secrets:
            for i, secret in enumerate(secrets, 1):
                value = secret.get("value", "")[:50] + "..." if len(secret.get("value", "")) > 50 else secret.get("value", "")
                secret_type = secret.get("type", "unknown")
                context = secret.get("context", "")
                print(f"  {i}. {secret_type}: {value}")
                if context:
                    print(f"     Context: {context[:100]}...")
        else:
            print("  No secrets found")
            
        print("\n📦 DEPENDENCIES DISCOVERED:")
        dependencies = analysis.get("dependencies", [])
        if dependencies:
            for i, dep in enumerate(dependencies, 1):
                url = dep.get("url", "")
                dep_type = dep.get("type", "unknown")
                print(f"  {i}. {dep_type}: {url}")
        else:
            print("  No dependencies found")
        
        print("\n📊 ANALYSIS STATISTICS:")
        stats = results.get("stats", {})
        for key, value in stats.items():
            print(f"  {key}: {value}")
        
        print(f"\n🔧 EXTRACTORS USED:")
        extractors = results.get("extractors_used", [])
        for extractor_name in extractors:
            print(f"  - {extractor_name}")
            
        return results
        
    except ImportError as e:
        print(f"Error importing extractor: {e}")
        print("Make sure you're running from the correct directory")
        return None
    except Exception as e:
        print(f"Error during analysis: {e}")
        return None

def main():
    url = "https://wishandwash.co.il/assets/index-BDSyL5Fh.js"
    
    print(f"🚀 Analyzing JavaScript Security - {url}")
    print("=" * 60)
    
    # Fetch content
    print("📥 Fetching JavaScript content...")
    content = fetch_js_content(url)
    if not content:
        print("❌ Failed to fetch content")
        return
    
    print(f"✅ Fetched {len(content)} bytes of JavaScript")
    
    # Analyze content
    print("\n🔍 Running comprehensive security analysis...")
    results = analyze_with_extractor(content)
    
    if not results:
        print("❌ Analysis failed")
        return
    
    print("\n✅ Analysis completed successfully!")

if __name__ == "__main__":
    main()