# 🔍 JavaScript Security Extractor

A comprehensive security analysis platform for **passive JavaScript reconnaissance** with automatic **sourcemap processing** and configurable **endpoint/secret analysis**. Built for security researchers and penetration testers.

## 🎯 **Core Features**

- **🌐 Passive Reconnaissance**: Chrome extension captures all JavaScript files during browsing
- **🗺️ Automatic Sourcemap Processing**: Always-on sourcemap detection, fetching, and original code reconstruction
- **📈 Sourcemap Validation Coverage**: Session/file lifecycle visibility for map detected/fetched/JSON-valid/processed states with grouped failure reasons
- **⚙️ Configurable Deep Analysis**: Toggle endpoint/secret analysis on demand
- **🔍 Multi-Engine Analysis**: REP-style extractors + Kingfisher rules + jsluice integration
- **📊 Real-time Dashboard**: Web interface for analysis results and file management
- **🔎 Fast List Filtering**: Files/Sessions search + status filters for large captures
- **🎛️ Analysis Run Controls**: Session Analyze-All and file Analyze/Reanalyze now require config confirmation (Quick or Advanced) before starting
- **🤖 Automated Recon Jobs**: Backend headless+parser job runner to discover and ingest JS/map assets without manual browsing
- **🔒 Security-First**: Comprehensive input validation and secure processing

## 🚀 **Quick Start**

### 1. **Start the Backend**
```bash
cd api
docker compose up -d
```
The supported Compose stack is PostgreSQL + FastAPI API. Celery/Redis worker services were removed; expensive work now uses FastAPI background tasks and DB-backed job records.

### 2. **Verify Installation**
```bash
curl http://localhost:3000/health
# Expected: {"status":"healthy"}
```

### 3. **Install Chrome Extension**
1. Open `chrome://extensions/`
2. Enable "Developer mode"
3. Click "Load unpacked" 
4. Select the `chrome-extension` folder

### 4. **Test the Flow**
```bash
# Test sourcemap processing
curl -X POST http://localhost:3000/api/analyze-comprehensive \
  -H "Content-Type: application/json" \
  -d '{
    "content": "fetch(\"/api/users\"); //# sourceMappingURL=app.js.map",
    "url": "https://wishandwash.co.il/app.js"
  }'
```

### Canonical Smoke Test Domain (Mandatory)
- Domain: `https://wishandwash.co.il`
- For sourcemap/capture/ingestion validation, use JS and MAP URLs from this domain and record exact URLs in task validation notes.
- Do not use `example.com` or legacy HoneyBook targets for new testing.

### Runtime Notes
- Startup runs `python -m alembic upgrade head` from the repository Alembic root.
- Existing dev databases stamped with legacy revision `0002` are supported by a no-op compatibility revision.
- On API startup, orphaned `queued`, `running`, or `cancelling` DB jobs from a previous process are marked terminal so old work does not remain visibly active forever.

### 5. **Configure Extension**
1. Click extension icon → Settings
2. Set API endpoint: `http://localhost:3000`
3. Configure `Analyze files on upload` (default off)

## 🔄 **Complete Workflow**

### **Passive Collection Phase**
1. **Browse Target Site** with extension enabled
2. **Extension Captures** all JavaScript files (including lazy-loaded)
3. **Automatic Upload** to API with batch processing

### **Processing Phase** 
4. **Always**: Sourcemap detection and processing
5. **Optional**: Endpoint and secret analysis (configurable)
6. **Storage**: Results saved to database with metadata

### **Analysis Phase**
7. **Dashboard Access**: View results via web interface
8. **API Access**: Query files, sessions, and analysis results
9. **Export**: Download captured files and analysis data

## 📊 **What Gets Extracted**

### 🗺️ **Sourcemap Processing (Always On)**
- **Detection**: Finds sourcemap URLs in `//# sourceMappingURL=` comments
- **Fetching**: Downloads remote sourcemaps or processes embedded data
- **Reconstruction**: Rebuilds original source files from sourcemap data
- **Results**: Shows sourcemap coverage and reconstructed file count

### 🎯 **Endpoints** (Optional/Configurable)
- **API Calls**: `fetch('/api/users')`, `axios.post('/data')`
- **WebSocket**: `new WebSocket('wss://wishandwash.co.il/ws')`
- **GraphQL**: `/graphql`, `/gql` endpoints
- **Dynamic URLs**: Template literals with variables

### 🔐 **Secrets & Credentials** (Optional/Configurable) 
- **API Keys**: Stripe (`sk_live_`), AWS (`AKIA`), GitHub tokens
- **JWT Tokens**: `eyJhbGciOiJIUzI1NiI...`
- **Database URLs**: `postgres://`, `mongodb://`, connection strings
- **Private Keys**: PEM format, certificates, SSH keys

### 🔗 **Dependencies & Structure**
- **ES6 Imports**: `import ... from '...'`
- **CommonJS**: `require('module')`
- **Dynamic Imports**: `import('chunk')`
- **Webpack Chunks**: Async module loading patterns

## 🌐 **Chrome Extension Usage**

### **Configuration Options**
- **Analysis Toggle**: Enable/disable endpoint and secret analysis
- **Analyze On Upload**: Stores `performAnalysisOnUpload` in extension settings (default `false`)
- **Sourcemap Processing**: Controlled by `captureSourceMaps` and `allowSourceMapFallback`
- **Domain Filtering**: Focus on specific domains
- **Auth Context Capture**: `captureAuthContext` captures allowlisted request auth headers + cookie presence metadata for sourcemap replay
- **Auth Context Domain Control**: `authContextDomains` restricts auth-context capture to explicit domains (leave empty for all in-scope domains)
- **REP+ Hint Import**: Optional import of REP+ script-like discovery hints into dependency queue
- **REP+ Extension ID**: Optional ID used for direct REP+ extension messaging
- **Auto-Upload**: Immediate API sending vs manual export

### **Manual Analysis**
```javascript
// In extension popup
1. Navigate to target site
2. Click "Start Capture"
3. Browse application (trigger dynamic loads)
4. Click "Stop Capture"  
5. Files automatically analyzed and uploaded
```

### **Background Collection**
```javascript
// Passive mode
- Extension runs continuously
- Captures all JavaScript requests
- Processes sourcemaps automatically
- Uploads based on configuration
```

## 🔧 **API Usage**

### **Core Analysis Endpoints**

#### **Comprehensive Analysis** (Full Pipeline)
```bash
POST /api/analyze-comprehensive
{
  "content": "fetch('/api/data'); const key='sk_live_123';",
  "url": "https://wishandwash.co.il/app.js",
  "options": {
    "include_sourcemap": true,
    "use_rep_endpoints": true,
    "use_rep_secrets": true
  }
}
```

#### **URL-Based Analysis** (Server-side Fetch)
```bash
POST /api/analyze-by-url
{
  "url": "https://wishandwash.co.il/app.js",
  "analysis_type": "comprehensive"
}
```

#### **Sourcemap Processing** (Standalone)
```bash
POST /api/process-sourcemap
{
  "js_url": "https://wishandwash.co.il/app.js",
  "sourcemap_url": "https://wishandwash.co.il/app.js.map"
}
```

### **File Management**
```bash
# Upload files (from extension)
POST /api/save-files
# Extension now sends `metadata.performAnalysis` (boolean) with each upload batch.
# If `metadata.performAnalysis=true`, ingestion runs file analysis and returns:
# - top-level `analysis` summary
# - per-file `files[i].analysis` status
# Extension file payloads may include optional `repPlusSummary` metadata when REP+ hint import is enabled.
# Extension file payloads may include optional `authContext` metadata for authenticated sourcemap replay.
# Backend sourcemap processing now attempts direct map fetch first, then auth-context replay fallback for auth-related failures.

# Get file metadata  
GET /api/files/{file_id}
# Response now includes `sourceMap` when a sourcemap record exists:
# {
#   "sourceMap": {
#     "mapUrl": "...",
#     "detectedMapUrl": "...",
#     "processingStatus": "pending|processing|completed|completed_limited|failed",
#     "processingError": "...",
#     "reconstructedFilesCount": 0,
#     "processedAt": null
#   }
# }
# `metadata.authContext` is redacted in this response (raw replay headers are not exposed).

# Get file content
GET /api/files/{file_id}/content

# Get analysis results
GET /api/files/{file_id}/analysis

# Get sourcemap results
GET /api/files/{file_id}/sourcemap
```

### **Session Management**
```bash
# List sessions
GET /api/sessions
# Each row now includes:
# {
#   "analysisSummary": {
#     "completed": 0,
#     "failed": 0,
#     "performed": false
#   },
#   "captureCoverage": {
#     "jobId": "latest-recon-job-id-or-null",
#     "jobStatus": "queued|running|completed|cancelled|failed",
#     "discovered_js": 0,
#     "fetched_js": 0,
#     "ingested_js": 0,
#     "analyzed_js": 0,
#     "map_detected": 0,
#     "map_fetched": 0,
#     "map_failed": 0,
#     "failure_reasons": {
#       "not_seen": 0,
#       "fetch_4xx": 0,
#       "fetch_5xx": 0,
#       "fetch_timeout": 0,
#       "non_js_content": 0,
#       "blocked_by_scope": 0,
#       "parse_failed": 0,
#       "dedup_skipped": 0
#     }
#   }
# }

# Get session files
GET /api/sessions/{session_id}/files
# Each file row includes `sourceMap` lifecycle state (or null when none exists)

# Get sourcemap validation coverage for a session
GET /api/sessions/{session_id}/sourcemap-validation
# Returns denominator-aware summary plus per-file validation lifecycle:
# {
#   "summary": {
#     "denominators": {"total_js": 0, "map_candidates": 0, "map_fetched": 0, "json_checked": 0},
#     "counts": {"no_map_candidate": 0, "processed": 0, "failed": 0, "json_valid": 0},
#     "rates": {"candidatePctOfJs": 0.0, "fetchPctOfCandidates": 0.0, "processPctOfCandidates": 0.0, "jsonValidPctOfFetched": 0.0},
#     "failure_reasons": {"decode_invalid_json": 0}
#   },
#   "files": [{"sourceMapId": "...", "validation": {"detected": true, "fetched": true, "json_valid": true, "processed": true}}]
# }

# Start async session-wide analysis
POST /api/sessions/{session_id}/analyze/start
# Request body options (snake_case or camelCase accepted):
# {
#   "options": {
#     "run_mode": "quick|advanced",
#     "analysis_type": "comprehensive|jsluice",
#     "include_sourcemap": true,
#     "resolve_urls": true,
#     "use_rep_endpoints": true,
#     "use_rep_secrets": true,
#     "use_jsluice_endpoints": false,
#     "use_jsluice_secrets": false,
#     "include_reconstructed_sources": true,
#     "continue_on_error": true,
#     "max_files_to_analyze": null,
#     "max_failures": null,
#     "per_file_timeout_ms": null,
#     "retry_attempts": 0
#   }
# }
# Response `job.options` echoes normalized run configuration.

# Poll async session analysis progress
GET /api/sessions/{session_id}/analyze/progress
# Active/completed jobs include `job.options` so the UI can show which run config was used.

# Stop async session analysis (cooperative cancel)
POST /api/sessions/{session_id}/analyze/stop

# Get comprehensive session analysis
GET /api/sessions/{session_id}/comprehensive-analysis
# Aggregates endpoints/secrets/dependencies and now includes source-file fallback metadata
# (e.g. `source_file_url` / `source_file_id`) for findings.
```

### **Automated Recon Jobs**
```bash
# Start a recon job (single URL or URLs array)
POST /api/recon/jobs/start
{
  "url": "https://wishandwash.co.il",
  "sessionId": "optional-existing-session-id",
  "sameOriginOnly": true,
  "maxAssets": 300,
  "maxDepth": 2,
  "includeSourceMaps": true,
  "performAnalysis": true
}

# List all recon jobs
GET /api/recon/jobs

# Get one job status + per-asset lifecycle
GET /api/recon/jobs/{job_id}
# Includes deterministic coverage counters + rates and miss-reason taxonomy

# Stop an active job
POST /api/recon/jobs/{job_id}/stop
```

## 📁 **Project Structure**

```
js-security-extractor/
├── AGENTS.md                          # Agent/session-start guidance
├── api/                              # FastAPI Backend
│   ├── app/
│   │   ├── api/routes/              # API endpoints
│   │   │   ├── enhanced_analysis.py    # Analysis endpoints
│   │   │   ├── ingestion.py            # File upload handling  
│   │   │   ├── files.py                # File management
│   │   │   ├── sessions.py             # Session management
│   │   │   ├── recon.py                # Automated recon job API
│   │   │   └── dashboard.py            # Web UI serving
│   │   ├── models/                  # Database models
│   │   │   ├── file.py                 # File metadata
│   │   │   ├── session.py              # Analysis sessions
│   │   │   ├── file_analysis.py        # Analysis results
│   │   │   └── source_map.py           # Sourcemap data
│   │   ├── services/                # Core processing
│   │   │   ├── comprehensive_extractor.py  # Multi-engine analysis
│   │   │   ├── native_sourcemap_processor.py  # Native sourcemap processing
│   │   │   ├── rep_endpoints_extractor.py   # Endpoint detection
│   │   │   ├── rep_secrets_extractor.py     # Secret detection (Kingfisher)
│   │   │   ├── recon_job_runner.py          # Headless/parser JS-map discovery runner
│   │   │   ├── security_utils.py           # Security framework
│   │   │   └── storage.py                  # File storage
│   │   ├── static/                  # Web dashboard assets
│   │   │   ├── dashboard.js            # Frontend JavaScript
│   │   │   └── dashboard.css           # Styling
│   │   └── templates/               # HTML templates
│   │       └── dashboard.html          # Web interface
│   ├── tests/                       # Test suite
│   │   ├── test_security_utils.py      # Security validation tests
│   │   ├── test_api_endpoints.py       # API integration tests
│   │   └── conftest.py                 # Test configuration
│   ├── docker-compose.yml           # Container orchestration
│   └── Dockerfile                   # API container
├── chrome-extension/                # Chrome Extension
│   ├── manifest.json               # Extension configuration
│   ├── background.js                # Service worker (main logic)
│   ├── popup.html/popup.js          # Extension UI
│   ├── options.html/options.js      # Settings interface  
│   └── modules/                     # Modular functionality
│       ├── content-fetcher.js          # HTTP interception
│       ├── sourcemap-detector.js       # Sourcemap discovery
│       ├── batch-uploader.js           # API communication
│       └── rep-plus-bridge.js          # REP+ integration bridge
├── APPLICATION_OVERVIEW.md          # Technical architecture doc
├── TODO.md                          # Current implementation roadmap
└── README.md                        # This file
```

## 🐳 **Docker Deployment**

### **Production Deployment**
```bash
# Start all services
docker compose up -d

# Check service status  
docker compose ps

# View logs
docker compose logs api --tail 20
```

### **Available Services**
- **api**: FastAPI server (port 3000)
- **postgres**: Database (port 5432)

### **Service Health Checks**
```bash
curl http://localhost:3000/health                    # API health
curl http://localhost:3000/api                       # API info
curl -f http://localhost:3000/dashboard              # Dashboard
```

## 🧪 **Testing & Validation**

### **Quick Validation**
```bash
# Health check
curl http://localhost:3000/health

# Test sourcemap processing
curl -X POST http://localhost:3000/api/analyze-by-url \
  -H "Content-Type: application/json" \
  -d '{"url": "https://wishandwash.co.il/app.js"}'

# Test file upload flow
curl -X POST http://localhost:3000/api/save-files \
  -H "Content-Type: application/json" \
  -d '{
    "files": [{
      "url": "test.js",
      "content": "fetch(\"/api/test\");",
      "contentHash": "abc123",
      "contentLength": 20,
      "sessionId": "test-session"
    }]
  }'
```

### **Comprehensive Tests**
```bash
cd api

# Run full test suite
python run_tests.py

# Security tests only
python run_tests.py --type security

# Performance tests
python run_tests.py --type performance
```

## 🔒 **Security Features**

### **🛡️ Input Validation**
- **Content Size Limits**: 10MB per file, prevents DoS
- **URL Validation**: Protocol and format validation  
- **Path Traversal Protection**: Secure file operations
- **Command Injection Prevention**: Secure subprocess execution

### **🔐 Processing Security**  
- **Sandboxed Execution**: Isolated external tool execution
- **Timeout Protection**: Prevents hanging processes
- **Resource Limits**: Memory and CPU constraints
- **Secure Cleanup**: Safe temporary file handling

### **📊 Data Protection**
- **Sensitive Data Sanitization**: Removes secrets from logs
- **Session-Based Access**: Isolated data access
- **Content Hashing**: Integrity verification
- **Audit Logging**: Security event tracking

## ⚙️ **Configuration**

### **Environment Variables**
```bash
# Database
DATABASE_URL=postgresql://user:pass@localhost:5432/js_extractor

# Storage
STORAGE_PATH=/app/storage
FILE_CONTENT_TTL_DAYS=30      # Stored JS content retention window
SOURCEMAP_CONTENT_TTL_DAYS=30 # Stored sourcemap content retention window
CLEANUP_MAX_DELETIONS_PER_RUN=500 # Safety cap per cleanup run
SOURCEMAP_PROCESSING_TIMEOUT_SECONDS=30 # Sourcemap processing timeout per file
SOURCEMAP_MAX_SIZE_BYTES=52428800       # Max sourcemap payload size (50MB)
SOURCEMAP_MAX_RECONSTRUCTED_FILES=1000  # Reconstructed-source cap per sourcemap

# Security
MAX_FILE_SIZE=10485760         # 10MB
MAX_TOTAL_SIZE=104857600       # 100MB  
ANALYSIS_TIMEOUT=300           # 5 minutes

# Features
ENABLE_SOURCEMAP_PROCESSING=true    # Always true
ENABLE_JSLUICE_INTEGRATION=true
ENABLE_REP_EXTRACTORS=true
```

### **Chrome Extension Settings**
```javascript
// In extension options
{
  "apiEndpoint": "http://localhost:3000",
  "performAnalysis": false,           // Toggle for endpoints/secrets
  "autoUpload": true,                // Immediate vs manual upload
  "domainFilter": ["wishandwash.co.il"],   // Target domains only
  "maxFileSize": 10485760,           // Size limits
  "batchSize": 5                     // Upload batch size  
}
```

## 🚨 **Troubleshooting**

### **Common Issues**

#### **API Won't Start**
```bash
# Check logs
docker-compose logs api

# Verify database
docker-compose exec postgres psql -U jsextractor -c "SELECT 1;"

# Reset database
docker-compose down && docker-compose up -d
```

#### **Extension Not Capturing**
```bash
# Check extension console (F12 in popup)
# Verify API endpoint in settings
curl http://localhost:3000/health

# Check permissions in chrome://extensions/
```

#### **Sourcemap Processing Fails**
```bash
# Check for network access to sourcemap URLs
curl -I https://wishandwash.co.il/app.js.map

# Verify processing in API logs
docker-compose logs api | grep sourcemap
```

### **Board Hygiene Check**
```bash
# Fails if TODO.md contains closed statuses (DONE/DROPPED)
bash scripts/check_todo_hygiene.sh
```

## 🎯 **Current Status & Roadmap**

### **✅ Implemented**
- ✅ Chrome extension passive collection
- ✅ Native Python sourcemap processing  
- ✅ REP-style endpoint/secret extraction
- ✅ Web dashboard interface
- ✅ Comprehensive security framework
- ✅ Docker deployment setup

### **🔄 In Progress** (See [TODO.md](TODO.md))
- 🔄 Automatic sourcemap processing on upload
- 🔄 Extension analysis configuration toggle
- 🔄 Dashboard sourcemap visualization  
- 🔄 Background task processing system

### **📋 Planned**
- 📋 Advanced session analytics
- 📋 Real-time processing notifications
- 📋 Enhanced error handling and recovery
- 📋 Performance optimizations

---

## 🎉 **You're Ready!**

Your JavaScript Security Extractor provides:
- ✅ **Passive Reconnaissance**: Comprehensive JavaScript capture during browsing
- ✅ **Automatic Sourcemap Processing**: Always-on original code reconstruction  
- ✅ **Configurable Analysis**: Optional endpoint and secret detection
- ✅ **Security-First Design**: Comprehensive validation and secure processing
- ✅ **Production Ready**: Docker deployment with monitoring

**Start your security analysis with confidence!** 🔍🔒

---

**⚠️ Use responsibly and only on applications you have permission to test.**
