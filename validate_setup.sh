#!/bin/bash

# JavaScript Security Extractor - Setup Validation Script
echo "🔍 JavaScript Security Extractor - Setup Validation"
echo "=================================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to check command success
check_result() {
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ $1${NC}"
        return 0
    else
        echo -e "${RED}❌ $1${NC}"
        return 1
    fi
}

echo ""
echo "📋 Checking Docker Services..."
echo "------------------------------"

# Check if Docker Compose is running
docker-compose ps > /dev/null 2>&1
check_result "Docker Compose accessible"

# Check individual services
echo ""
echo "🐳 Service Status:"
docker-compose ps | grep -E "(postgres|redis|api|celery_worker)" | while read line; do
    if echo "$line" | grep -q "Up"; then
        service=$(echo "$line" | awk '{print $1}')
        echo -e "${GREEN}✅ $service running${NC}"
    else
        service=$(echo "$line" | awk '{print $1}')
        echo -e "${RED}❌ $service not running${NC}"
    fi
done

echo ""
echo "🌐 API Connectivity Tests..."
echo "-----------------------------"

# Test health endpoint
response=$(curl -s -w "%{http_code}" http://localhost:3000/health -o /tmp/health_response 2>/dev/null)
if [ "$response" = "200" ]; then
    check_result "API Health Check (GET /health)"
else
    echo -e "${RED}❌ API Health Check - HTTP $response${NC}"
fi

# Test root endpoint
response=$(curl -s -w "%{http_code}" http://localhost:3000/ -o /tmp/root_response 2>/dev/null)
if [ "$response" = "200" ]; then
    check_result "API Root Endpoint (GET /)"
else
    echo -e "${RED}❌ API Root Endpoint - HTTP $response${NC}"
fi

echo ""
echo "🔧 Security Tools Verification..."
echo "---------------------------------"

# Test jsluice binary
docker exec api-api-1 jsluice --help > /dev/null 2>&1
check_result "jsluice binary available"

# Test sourcemapper binary
docker exec api-api-1 sourcemapper -h > /dev/null 2>&1
check_result "sourcemapper binary available"

echo ""
echo "🧪 Functional Tests..."
echo "----------------------"

# Test comprehensive analysis endpoint
test_payload='{"content": "fetch(\"/api/users\"); const key = \"sk_live_123\";", "url": "https://example.com/test.js"}'
response=$(curl -s -w "%{http_code}" -X POST http://localhost:3000/api/analyze-comprehensive \
    -H "Content-Type: application/json" \
    -d "$test_payload" \
    -o /tmp/analysis_response 2>/dev/null)

if [ "$response" = "200" ]; then
    # Check if analysis found expected results
    if grep -q "api/users" /tmp/analysis_response && grep -q "sk_live_123" /tmp/analysis_response; then
        check_result "Comprehensive Analysis (finds URLs and secrets)"
    else
        echo -e "${YELLOW}⚠️  Comprehensive Analysis endpoint works but may not be finding expected patterns${NC}"
    fi
else
    echo -e "${RED}❌ Comprehensive Analysis - HTTP $response${NC}"
fi

# Test file ingestion
ingestion_payload='{"metadata": {"sessionId": "test-session-123"}, "files": [{"url": "https://example.com/test.js", "contentHash": "test123", "sessionId": "test-session-123", "contentType": "application/javascript", "contentLength": 50, "content": "console.log(\"test\");"}]}'
response=$(curl -s -w "%{http_code}" -X POST http://localhost:3000/api/save-files \
    -H "Content-Type: application/json" \
    -d "$ingestion_payload" \
    -o /tmp/ingestion_response 2>/dev/null)

if [ "$response" = "200" ]; then
    check_result "File Ingestion (POST /api/save-files)"
else
    echo -e "${RED}❌ File Ingestion - HTTP $response${NC}"
fi

echo ""
echo "🔒 Security Validation..."
echo "-------------------------"

# Test malicious payload rejection
malicious_payload='{"content": "; rm -rf /", "url": "malicious"}'
response=$(curl -s -w "%{http_code}" -X POST http://localhost:3000/api/analyze-comprehensive \
    -H "Content-Type: application/json" \
    -d "$malicious_payload" \
    -o /tmp/security_response 2>/dev/null)

if [ "$response" = "400" ] || [ "$response" = "422" ]; then
    check_result "Security Validation (rejects malicious payloads)"
else
    echo -e "${YELLOW}⚠️  Security validation may not be working properly - HTTP $response${NC}"
fi

echo ""
echo "📊 Performance Check..."
echo "-----------------------"

# Quick performance test
start_time=$(date +%s%N)
curl -s -X POST http://localhost:3000/api/analyze-jsluice \
    -H "Content-Type: application/json" \
    -d '{"content": "fetch(\"/fast-test\");", "url": "test.js"}' \
    > /dev/null 2>&1
end_time=$(date +%s%N)
duration=$(( (end_time - start_time) / 1000000 ))

if [ $duration -lt 5000 ]; then
    check_result "Performance (analysis < 5s): ${duration}ms"
else
    echo -e "${YELLOW}⚠️  Performance may be slow: ${duration}ms${NC}"
fi

echo ""
echo "📁 Chrome Extension Check..."
echo "-----------------------------"

if [ -f "chrome-extension/manifest.json" ]; then
    check_result "Chrome extension files present"
    
    # Check manifest version
    if grep -q '"manifest_version": 3' chrome-extension/manifest.json; then
        check_result "Manifest V3 format"
    else
        echo -e "${YELLOW}⚠️  Manifest may not be V3 format${NC}"
    fi
else
    echo -e "${RED}❌ Chrome extension files not found${NC}"
fi

echo ""
echo "🧹 Cleanup..."
echo "-------------"
rm -f /tmp/health_response /tmp/root_response /tmp/analysis_response /tmp/ingestion_response /tmp/security_response

echo ""
echo "📋 Summary"
echo "=========="
echo -e "${GREEN}✅ = Working correctly${NC}"
echo -e "${YELLOW}⚠️  = Warning or needs attention${NC}" 
echo -e "${RED}❌ = Failed or not working${NC}"

echo ""
echo "🚀 Next Steps:"
echo "1. If all checks passed: You're ready to use the tool!"
echo "2. Load the Chrome extension: chrome://extensions/ → Load unpacked"
echo "3. Run comprehensive tests: cd api && python run_tests.py"
echo "4. Start analyzing JavaScript: Use the extension or API directly"

echo ""
echo "📚 Documentation: See README.md for complete usage guide"
echo "🆘 Support: Check troubleshooting section if issues persist"