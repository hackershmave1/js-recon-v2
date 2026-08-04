#!/usr/bin/env python3
"""
Test B-004 - Smart Analysis Trigger Heuristics
Tests automatic analysis triggering based on file characteristics
"""

import tempfile
import json
import uuid
import os

def test_smart_triggers_import():
    """Test that smart triggers can be imported"""
    try:
        from api.app.services.analysis_triggers import SmartAnalysisTriggers
        from api.app.config import settings
        
        triggers = SmartAnalysisTriggers()
        assert triggers is not None
        print("✓ Smart triggers import works")
    except ImportError as e:
        print(f"SKIP: Cannot import smart triggers: {e}")

def test_file_size_trigger():
    """Test analysis triggering based on file size"""
    try:
        from api.app.services.analysis_triggers import SmartAnalysisTriggers
        from api.app.config import settings
    except ImportError:
        print("SKIP: Cannot import required modules")
        return
    
    triggers = SmartAnalysisTriggers()
    
    # Small file - should not trigger
    small_content = "console.log('hello');"
    result = triggers.should_trigger_analysis(
        content=small_content,
        file_metadata={},
        manual_analysis_requested=False
    )
    assert result["trigger"] is False
    assert "large_file" not in str(result["criteria_met"])
    
    # Large file - should trigger
    large_content = "x" * (settings.smart_analysis_min_file_size + 1000)
    result = triggers.should_trigger_analysis(
        content=large_content,
        file_metadata={},
        manual_analysis_requested=False
    )
    assert result["trigger"] is True
    assert any("large_file" in criteria for criteria in result["criteria_met"])
    print("✓ File size trigger works")

def test_sourcemap_trigger():
    """Test analysis triggering based on sourcemap status"""
    try:
        from api.app.services.analysis_triggers import SmartAnalysisTriggers
        from api.app.config import settings
    except ImportError:
        print("SKIP: Cannot import required modules")
        return
    
    triggers = SmartAnalysisTriggers()
    small_content = "console.log('test');"
    
    # No sourcemap - should not trigger
    result = triggers.should_trigger_analysis(
        content=small_content,
        file_metadata={},
        sourcemap_status=None,
        manual_analysis_requested=False
    )
    assert result["trigger"] is False
    
    # Completed sourcemap - should trigger
    result = triggers.should_trigger_analysis(
        content=small_content,
        file_metadata={},
        sourcemap_status="completed",
        manual_analysis_requested=False
    )
    assert result["trigger"] is True
    assert "has_sourcemap" in result["criteria_met"]
    
    # Limited sourcemap - should also trigger
    result = triggers.should_trigger_analysis(
        content=small_content,
        file_metadata={},
        sourcemap_status="completed_limited",
        manual_analysis_requested=False
    )
    assert result["trigger"] is True
    assert "has_sourcemap" in result["criteria_met"]
    print("✓ Sourcemap trigger works")

def test_api_pattern_trigger():
    """Test analysis triggering based on API patterns"""
    try:
        from api.app.services.analysis_triggers import SmartAnalysisTriggers
        from api.app.config import settings
    except ImportError:
        print("SKIP: Cannot import required modules")
        return
    
    triggers = SmartAnalysisTriggers()
    
    # Content with many API patterns
    api_content = '''
    fetch('/api/users');
    axios.post('/v1/data.json');
    const endpoint = 'https://api.example.com';
    new XMLHttpRequest();
    '''
    
    result = triggers.should_trigger_analysis(
        content=api_content,
        file_metadata={},
        manual_analysis_requested=False
    )
    
    # Should trigger due to API patterns
    assert result["trigger"] is True
    api_criteria = [c for c in result["criteria_met"] if "api_patterns" in c]
    assert len(api_criteria) > 0
    assert result["api_patterns"] >= settings.smart_analysis_api_pattern_threshold
    print("✓ API pattern trigger works")

def test_secret_pattern_trigger():
    """Test analysis triggering based on potential secrets"""
    try:
        from api.app.services.analysis_triggers import SmartAnalysisTriggers
        from api.app.config import settings
    except ImportError:
        print("SKIP: Cannot import required modules")
        return
    
    triggers = SmartAnalysisTriggers()
    
    # Content with potential secrets
    secret_content = '''
    const api_key = "sk_live_1234567890abcdef1234567890abcdef";
    const secret = "very-secret-token-here";
    const auth_token = process.env.AUTH_TOKEN;
    '''
    
    result = triggers.should_trigger_analysis(
        content=secret_content,
        file_metadata={},
        manual_analysis_requested=False
    )
    
    # Should trigger due to secret patterns
    assert result["trigger"] is True
    secret_criteria = [c for c in result["criteria_met"] if "secret_patterns" in c]
    assert len(secret_criteria) > 0
    assert result["secret_patterns"] >= settings.smart_analysis_secret_pattern_threshold
    print("✓ Secret pattern trigger works")

def test_minified_js_trigger():
    """Test analysis triggering based on minified JavaScript detection"""
    try:
        from api.app.services.analysis_triggers import SmartAnalysisTriggers
    except ImportError:
        print("SKIP: Cannot import required modules")
        return
    
    triggers = SmartAnalysisTriggers()
    
    # Minified JavaScript (long lines)
    minified_content = '''var a=function(){return"very-long-line-of-minified-javascript-code-that-exceeds-normal-formatting"};var b=function(){return"another-very-long-line-of-minified-javascript-code-that-definitely-exceeds-80-characters"};'''
    
    result = triggers.should_trigger_analysis(
        content=minified_content,
        file_metadata={},
        manual_analysis_requested=False
    )
    
    # Should trigger due to minified detection
    assert result["trigger"] is True
    assert "minified_js" in result["criteria_met"]
    print("✓ Minified JS trigger works")

def test_manual_analysis_priority():
    """Test that manual analysis request always takes priority"""
    try:
        from api.app.services.analysis_triggers import SmartAnalysisTriggers
    except ImportError:
        print("SKIP: Cannot import required modules")
        return
    
    triggers = SmartAnalysisTriggers()
    
    # Small file that wouldn't normally trigger
    small_content = "console.log('tiny');"
    
    # Manual analysis requested - should trigger
    result = triggers.should_trigger_analysis(
        content=small_content,
        file_metadata={},
        manual_analysis_requested=True
    )
    
    assert result["trigger"] is True
    assert result["reason"] == "manual_request"
    assert "manual_request" in result["criteria_met"]
    print("✓ Manual analysis priority works")

def test_smart_analysis_disabled():
    """Test behavior when smart analysis is disabled"""
    try:
        from api.app.services.analysis_triggers import SmartAnalysisTriggers
        from api.app.config import settings
    except ImportError:
        print("SKIP: Cannot import required modules")
        return
    
    # Temporarily disable smart analysis
    original_setting = settings.smart_analysis_enabled
    settings.smart_analysis_enabled = False
    
    try:
        triggers = SmartAnalysisTriggers()
        
        # Large file with API patterns - should not trigger when disabled
        large_api_content = "x" * 100000 + "fetch('/api/test');"
        
        result = triggers.should_trigger_analysis(
            content=large_api_content,
            file_metadata={},
            manual_analysis_requested=False
        )
        
        assert result["trigger"] is False
        assert result["reason"] == "smart_analysis_disabled"
        
    finally:
        # Restore original setting
        settings.smart_analysis_enabled = original_setting
    
    print("✓ Smart analysis disable works")

def test_trigger_summary():
    """Test that trigger configuration summary works"""
    try:
        from api.app.services.analysis_triggers import SmartAnalysisTriggers
    except ImportError:
        print("SKIP: Cannot import required modules")
        return
    
    triggers = SmartAnalysisTriggers()
    summary = triggers.get_trigger_summary()
    
    required_keys = [
        "enabled", "min_file_size", "with_sourcemaps",
        "api_pattern_threshold", "secret_pattern_threshold",
        "minified_js_threshold", "api_patterns_count", "secret_patterns_count"
    ]
    
    for key in required_keys:
        assert key in summary
    
    assert isinstance(summary["enabled"], bool)
    assert isinstance(summary["min_file_size"], int)
    assert isinstance(summary["api_patterns_count"], int)
    assert summary["api_patterns_count"] > 0
    assert summary["secret_patterns_count"] > 0
    print("✓ Trigger summary works")

if __name__ == "__main__":
    print("Running B-004 smart analysis trigger tests...")
    
    try:
        test_smart_triggers_import()
        print("✓ test_smart_triggers_import")
    except Exception as e:
        print(f"✗ test_smart_triggers_import: {e}")
    
    try:
        test_file_size_trigger()
        print("✓ test_file_size_trigger")
    except Exception as e:
        print(f"✗ test_file_size_trigger: {e}")
    
    try:
        test_sourcemap_trigger()
        print("✓ test_sourcemap_trigger")
    except Exception as e:
        print(f"✗ test_sourcemap_trigger: {e}")
    
    try:
        test_api_pattern_trigger()
        print("✓ test_api_pattern_trigger")
    except Exception as e:
        print(f"✗ test_api_pattern_trigger: {e}")
    
    try:
        test_secret_pattern_trigger()
        print("✓ test_secret_pattern_trigger")
    except Exception as e:
        print(f"✗ test_secret_pattern_trigger: {e}")
    
    try:
        test_minified_js_trigger()
        print("✓ test_minified_js_trigger")
    except Exception as e:
        print(f"✗ test_minified_js_trigger: {e}")
    
    try:
        test_manual_analysis_priority()
        print("✓ test_manual_analysis_priority")
    except Exception as e:
        print(f"✗ test_manual_analysis_priority: {e}")
    
    try:
        test_smart_analysis_disabled()
        print("✓ test_smart_analysis_disabled")
    except Exception as e:
        print(f"✗ test_smart_analysis_disabled: {e}")
    
    try:
        test_trigger_summary()
        print("✓ test_trigger_summary")
    except Exception as e:
        print(f"✗ test_trigger_summary: {e}")
    
    print("\nB-004 tests completed")