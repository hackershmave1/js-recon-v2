#!/usr/bin/env python3
"""
Test runner script for JS Security Extractor API.
Provides comprehensive test execution with security focus.
"""
import sys
import os
import subprocess
import argparse
import json
from pathlib import Path

# Add app directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "app"))


def run_security_tests():
    """Run security-focused tests."""
    print("🔒 Running security tests...")
    cmd = [
        "python", "-m", "pytest",
        "-m", "security",
        "--verbose",
        "--tb=short",
        "--cov=app.services.security_utils",
        "--cov-report=term-missing"
    ]
    return subprocess.run(cmd, capture_output=True, text=True)


def run_unit_tests():
    """Run unit tests."""
    print("🧪 Running unit tests...")
    cmd = [
        "python", "-m", "pytest", 
        "-m", "unit",
        "--verbose",
        "--cov=app",
        "--cov-report=html:htmlcov",
        "--cov-report=term-missing"
    ]
    return subprocess.run(cmd, capture_output=True, text=True)


def run_integration_tests():
    """Run integration tests."""
    print("🔗 Running integration tests...")
    cmd = [
        "python", "-m", "pytest",
        "-m", "integration",
        "--verbose",
        "--tb=long"
    ]
    return subprocess.run(cmd, capture_output=True, text=True)


def run_all_tests():
    """Run all tests."""
    print("🚀 Running all tests...")
    cmd = [
        "python", "-m", "pytest",
        "--verbose",
        "--cov=app",
        "--cov-report=html:htmlcov",
        "--cov-report=term-missing",
        "--cov-report=xml",
        "--maxfail=10",
        "--durations=10"
    ]
    return subprocess.run(cmd, capture_output=True, text=True)


def run_smoke_tests():
    """Run basic smoke tests."""
    print("💨 Running smoke tests...")
    cmd = [
        "python", "-m", "pytest",
        "-k", "test_health or test_root",
        "--verbose",
        "--tb=short"
    ]
    return subprocess.run(cmd, capture_output=True, text=True)


def run_performance_tests():
    """Run performance tests."""
    print("⚡ Running performance tests...")
    cmd = [
        "python", "-m", "pytest",
        "-m", "slow",
        "--verbose",
        "--durations=0"
    ]
    return subprocess.run(cmd, capture_output=True, text=True)


def check_dependencies():
    """Check if required test dependencies are available."""
    required_packages = [
        'pytest', 'pytest-cov', 'pytest-asyncio', 
        'httpx', 'fastapi', 'sqlalchemy'
    ]
    
    missing = []
    for package in required_packages:
        try:
            __import__(package.replace('-', '_'))
        except ImportError:
            missing.append(package)
    
    if missing:
        print(f"❌ Missing required packages: {', '.join(missing)}")
        print("Install with: pip install " + " ".join(missing))
        return False
    
    print("✅ All required test dependencies are available")
    return True


def validate_environment():
    """Validate test environment setup."""
    print("🔍 Validating test environment...")
    
    # Check if we're in a test environment
    if os.getenv('TESTING') != 'true':
        os.environ['TESTING'] = 'true'
        print("⚠️  Set TESTING=true environment variable")
    
    # Set test database URL if not set
    if not os.getenv('DATABASE_URL'):
        os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
        print("⚠️  Using in-memory SQLite for testing")
    
    # Set test storage path
    if not os.getenv('STORAGE_PATH'):
        os.environ['STORAGE_PATH'] = '/tmp/js-extractor-test'
        print("⚠️  Using /tmp for test storage")
    
    print("✅ Test environment validated")
    return True


def generate_test_report(results):
    """Generate a comprehensive test report."""
    report = {
        'timestamp': subprocess.run(['date'], capture_output=True, text=True).stdout.strip(),
        'environment': {
            'python_version': sys.version,
            'platform': sys.platform,
            'working_directory': os.getcwd()
        },
        'test_results': {}
    }
    
    for test_type, result in results.items():
        report['test_results'][test_type] = {
            'exit_code': result.returncode,
            'passed': result.returncode == 0,
            'stdout': result.stdout,
            'stderr': result.stderr
        }
    
    # Save report
    with open('test_report.json', 'w') as f:
        json.dump(report, f, indent=2)
    
    print("📊 Test report saved to test_report.json")


def main():
    """Main test runner."""
    parser = argparse.ArgumentParser(description='JS Security Extractor Test Runner')
    parser.add_argument('--type', choices=['all', 'unit', 'integration', 'security', 'smoke', 'performance'], 
                       default='all', help='Type of tests to run')
    parser.add_argument('--check-deps', action='store_true', help='Check dependencies only')
    parser.add_argument('--validate-env', action='store_true', help='Validate environment only')
    parser.add_argument('--report', action='store_true', help='Generate detailed test report')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    
    args = parser.parse_args()
    
    # Check dependencies
    if args.check_deps:
        return 0 if check_dependencies() else 1
    
    # Validate environment
    if args.validate_env:
        return 0 if validate_environment() else 1
    
    # Validate environment for test runs
    if not validate_environment():
        return 1
    
    if not check_dependencies():
        return 1
    
    # Run tests based on type
    results = {}
    exit_code = 0
    
    if args.type == 'all':
        results['all'] = run_all_tests()
    elif args.type == 'unit':
        results['unit'] = run_unit_tests()
    elif args.type == 'integration':
        results['integration'] = run_integration_tests()
    elif args.type == 'security':
        results['security'] = run_security_tests()
    elif args.type == 'smoke':
        results['smoke'] = run_smoke_tests()
    elif args.type == 'performance':
        results['performance'] = run_performance_tests()
    
    # Check results
    for test_type, result in results.items():
        if result.returncode != 0:
            print(f"❌ {test_type.upper()} tests failed")
            if args.verbose:
                print("STDOUT:", result.stdout)
                print("STDERR:", result.stderr)
            exit_code = 1
        else:
            print(f"✅ {test_type.upper()} tests passed")
            if args.verbose:
                print("STDOUT:", result.stdout)
    
    # Generate report if requested
    if args.report:
        generate_test_report(results)
    
    # Summary
    if exit_code == 0:
        print("\n🎉 All tests passed successfully!")
    else:
        print("\n💥 Some tests failed. Check the output above for details.")
    
    return exit_code


if __name__ == '__main__':
    sys.exit(main())