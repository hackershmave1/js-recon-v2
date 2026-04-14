# JavaScript Security Extractor - Application Overview

## Project Summary

**JavaScript Security Extractor** is a comprehensive security analysis platform designed for passive JavaScript reconnaissance. The application provides both a web-based dashboard and API endpoints for analyzing JavaScript files to extract URLs, secrets, dependencies, and security patterns. It supports source map reconstruction, Chrome extension integration, and comprehensive security analysis using multiple extraction engines.

## Architecture Overview

### Technology Stack
- **Backend**: FastAPI (Python 3.11+)
- **Database**: PostgreSQL with SQLAlchemy ORM
- **Frontend**: Bootstrap 5 + Vanilla JavaScript
- **Deployment**: Docker + Docker Compose
- **Security Tools**: jsluice, sourcemapper (external binaries)
- **Task Processing**: Celery (optional background processing)

### Core Components

#### 1. Backend API (`/app/`)
**Main Application** (`app/main.py:16-80`)
- FastAPI application with CORS middleware
- CORS is explicitly scoped to local dashboard origins (`localhost`/`127.0.0.1`) plus Chrome extension origins (`chrome-extension://<id>`) for credentialed browser requests
- Static file serving for dashboard assets
- Health check and API discovery endpoints
- Database schema migration handling

**API Routes**:
- **Dashboard Router** (`app/api/routes/dashboard.py`) - Serves web UI
- **Ingestion Router** (`app/api/routes/ingestion.py:49-129`) - File upload and storage
- **Files Router** (`app/api/routes/files.py:26-293`) - File management and analysis 
- **Sessions Router** (`app/api/routes/sessions.py`) - Session management
- **Enhanced Analysis Router** (`app/api/routes/enhanced_analysis.py:51-568`) - Advanced analysis endpoints
- **Recon Router** (`app/api/routes/recon.py`) - Automated recon job lifecycle (`start`, `list`, `status`, `stop`)
- **Asset Graph Router** (`app/api/routes/asset_graph.py`) - Asset discovery provenance tracking (`asset-graph`, `ancestry`, `descendants`, `gaps`, `stats`)

#### 2. Database Models (`/app/models/`)
**Core Models**:
- **Session** (`models/session.py`) - Analysis session grouping
- **File** (`models/file.py:11-29`) - JavaScript file storage and metadata
- **FileAnalysis** (`models/file_analysis.py`) - Analysis results storage
- **Dependency** (`models/dependency.py`) - JavaScript dependency tracking
- **SourceMap** (`models/source_map.py`) - Source map processing results and lifecycle state
  - `detected_map_url`, `processing_status`, `processing_error`
  - `reconstructed_files_count`, `processed_at`
- **AssetNode** (`models/asset_graph.py`) - Asset discovery nodes with provenance metadata
  - `url`, `asset_type`, `discovery_depth`, `fetch_attempted`, `processed`, `asset_metadata`
- **AssetEdge** (`models/asset_graph.py`) - Asset discovery relationships
  - `source_node_id`, `target_node_id`, `discovery_method`, `referer`, `initiator`, `context_metadata`

#### 3. Security Analysis Services (`/app/services/`)
**ComprehensiveExtractor** (`services/comprehensive_extractor.py:12-404`)
- Unified analysis engine combining multiple extractors
- Defaults to REP-style endpoint + Kingfisher secret extraction
- Supports optional jsluice extractors, custom patterns, and source map reconstruction
- Deduplication and normalization of results across tools

**AssetGraphService** (`services/asset_graph_service.py`)
- Asset discovery provenance tracking and graph management
- Creates nodes/edges for discovered assets with discovery metadata
- Provides ancestry tracking, descendant queries, and discovery gap analysis
- Supports status updates and file linking for comprehensive asset tracking

**ParameterExtractor** (`services/parameter_extractor.py`)
- Parameter name extraction from JavaScript, JSON, XML, HTML, and URL sources
- Confidence scoring and provenance tracking for parameter discovery
- Supports variable declarations, function parameters, object properties, form fields
- Deduplication with highest confidence preservation for attack surface analysis

**Security Utilities** (`services/security_utils.py:16-423`)
- Input validation for URLs, file paths, and JavaScript content
- Secure subprocess execution with timeout and size limits
- Temporary file management with secure cleanup
- Security configuration and logging sanitization

**JSluice Integration** (`services/jsluice_extractor.py`)
- Secure wrapper around jsluice binary for URL/secret extraction
- Process isolation and output validation

**SourceMap Processor** (`services/sourcemap_processor.py`)
- Source map parsing and original file reconstruction
- Integration with sourcemapper tool

**Recon Job Runner** (`services/recon_job_runner.py`)
- Optional Playwright response interception for runtime JS discovery
- HTML/inline/JS-reference fallback discovery when headless runtime is unavailable
- Deterministic asset lifecycle state tracking (`discovered`, `fetched`, `ingested`, `analyzed`) with failure reasons
- Ingestion-path reuse via `/api/save-files` to keep storage and analysis behavior consistent

#### 4. Web Dashboard (`/app/static/` & `/app/templates/`)
**Dashboard Interface** (`templates/dashboard.html:1-50+`)
- Bootstrap-based responsive design
- Real-time analysis results display
- Session management and file browsing
- Drag-and-drop file upload

**JavaScript Frontend** (`static/dashboard.js`)
- SecurityDashboard class with modular analysis features
- Real-time API communication with error handling
- Dynamic UI updates and result visualization
- Failed analysis states now include source-aware explanations (`analysis`, `sourcemap`, `capture/fetch`) with retry guidance in Files and Analysis context
- Sessions Analyze-All now uses background start + progress polling so navigation remains usable during long-running session analysis
- Analyze-All action now opens a per-run configuration modal (Quick preset or Advanced controls) before execution, with local defaults persistence
- File-level Analyze/Reanalyze/Retry actions in View Files also open a per-run configuration modal before execution
- Active Analyze-All jobs can be stopped from Sessions UI; cancellation is cooperative and lands in explicit `cancelling` -> `cancelled` lifecycle states
- Session/file progress polling applies incremental row patches (instead of full-list rebuilds) to avoid flicker and preserve scroll/selection state during active analysis
- Progress badges now include run-context labels (mode/type/maps/error policy) sourced from `job.options` for traceability
- View Files now runs bounded auto-polling for in-progress upload/sourcemap/analysis rows so lifecycle badges/actions update without manual refresh
- View Files includes a sourcemap validation summary panel with denominator-aware coverage metrics and grouped failure classes
- Files and Sessions tabs now expose in-place search/status filters with clear actions to reduce list noise without leaving the current tab context
- Analysis Context card now includes `Back to Session Files` for stored-result views, restoring session-scoped file navigation in one click
- Sessions list now includes persisted analysis summary counts (`completed`, `failed`, `performed`) and exposes a summary modal with endpoint/secret source context (`file:line` when available)
- Files and Sessions views now support checkbox multi-select + bulk delete actions with partial-failure feedback

**Styling** (`static/dashboard.css`)
- Custom CSS with professional theming
- Responsive design and loading animations

## Key Features

### 1. File Analysis and Storage
**Multi-Format Support**:
- JavaScript files (.js, .mjs, .jsx, .ts, .tsx)
- Source maps (.map) with reconstruction capabilities
- Content hashing for deduplication
- Metadata preservation and dependency tracking

**Storage System** (`services/storage.py`):
- Secure file storage with configurable paths
- Content-based organization by session and hash
- TTL-driven content cleanup workflow (`services/retention_cleanup.py`) for JS/map artifacts

### 2. Security Analysis Engines
**Comprehensive Analysis** (`enhanced_analysis.py:51-97`):
- Combines multiple extraction methods
- Real-time and background processing options
- Batch analysis support
- Session-level aggregation and insights

**URL and Endpoint Extraction**:
- JavaScript API calls (fetch, axios, XHR)
- Absolute and relative URL detection  
- URL resolution and normalization
- Custom pattern matching

**Secret Detection**:
- API keys, tokens, passwords
- High-confidence pattern matching
- Context preservation for validation

**Dependency Analysis**:
- ES6 imports, CommonJS requires
- Dynamic imports and AMD modules
- Webpack chunk analysis
- Dependency resolution and mapping

### 3. Source Map Processing
**Reconstruction Capabilities**:
- Original source file recovery from source maps
- Support for URL-based and embedded source maps
- Recursive analysis of reconstructed files
- Source location mapping preservation

### 4. Security Features
**Input Validation** (`security_utils.py:46-194`):
- Content size and format validation
- URL safety checking with pattern blocking
- Path traversal protection
- File extension whitelisting

**Process Security** (`security_utils.py:196-288`):
- Sandboxed external tool execution
- Timeout and resource limits
- Secure temporary file handling
- Environment isolation

**Data Protection**:
- Sensitive data sanitization in logs
- Secure file cleanup with overwriting
- Session-based access control

### 5. API Endpoints

#### Analysis Endpoints
- `POST /api/analyze-comprehensive` - Full analysis with all extractors
- `POST /api/analyze-jsluice` - Fast URL/secret extraction only
- `POST /api/analyze-by-url` - Server-side URL fetching and analysis
- `POST /api/batch-analyze` - Multiple file analysis
- `POST /api/process-sourcemap` - Source map reconstruction

#### File Management
- `POST /api/save-files` - File upload and storage (`ingestion.py:49-129`)
  - Extension upload metadata includes `performAnalysis` from `performAnalysisOnUpload` setting
  - When `performAnalysis=true`, ingestion runs `ComprehensiveExtractor`, stores `FileAnalysis`, and returns analysis status in response
  - Ingestion validates file URL/content/sourcemap URL/dependency `resolvedUrl` and rejects malformed payloads with `422`
  - Ingestion now prefers uploaded `sourceMapContent` for sourcemap processing and only falls back to remote `sourceMapUrl` fetch when content is not provided
  - Ingestion accepts optional `files[].authContext` (strict schema with allowlisted replay headers + cookie presence metadata) from extension uploads
  - Sourcemap URL processing now tries direct fetch first, then authenticated header replay fallback for auth-related fetch failures (`401/403/4xx/network/timeout`) when valid auth context is available
  - Sourcemap processing now applies bounded retry/backoff for transient fetch failures and classifies `processingError` values (for example `[fetch_http_404]`, `[fetch_http_5xx]`, `[decode_invalid_json]`)
- `GET /api/files/{file_id}` - File metadata retrieval (`files.py`) including `sourceMap` lifecycle state:
  - `mapUrl`, `detectedMapUrl`, `processingStatus`, `processingError`
  - `reconstructedFilesCount`, `processedAt`, `parsed`
  - `processingStatus` values include `completed_limited` when sourcemap parsing succeeds but reconstructed files are capped by resource limits.
  - Internal filesystem fields are not exposed in public DTOs (`storedPath`/`mapPath` removed)
  - `metadata.authContext` is redacted in API responses (raw replay headers are never exposed by `GET /api/files/{file_id}`)
- `GET /api/files/{file_id}/content` - File content download (`files.py:48-54`)
- `GET /api/files/{file_id}/dependencies` - Dependency analysis (`files.py:57-101`)
- `POST /api/files/{file_id}/analyze` - Individual file analysis (`files.py:104-185`)
- `GET /api/files/{file_id}/analysis` - Analysis results retrieval (`files.py:188-213`)
- `DELETE /api/files/{file_id}` - File deletion with cleanup (`files.py:216-274`)
  - Delete flow now explicitly removes dependent dependency/analysis/sourcemap rows before file removal to avoid FK failures from duplicate sourcemap associations.
  - Delete response returns aggregate artifact count and does not expose raw deleted storage paths.
- `POST /api/files/bulk-delete` - Bulk file deletion with per-id success/failure reporting

#### Session Management
- Session creation, listing, and deletion
- `GET /api/sessions/{session_id}/files` now includes per-file `sourceMap` lifecycle state in addition to analysis status/counts
- `GET /api/sessions/{session_id}/sourcemap-validation` returns session-level sourcemap coverage metrics and per-file validation lifecycle (`detected`, `fetched`, `http_status`, `content_type`, `json_valid`, `processed`, `failure_class`)
- `GET /api/sessions` now includes `analysisSummary` for each session (`completed`, `failed`, `performed`)
- `GET /api/sessions` now includes `captureCoverage` from latest recon job for the session (when available), including counters/rates and deterministic miss-reason taxonomy
- `POST /api/sessions/{session_id}/analyze/start` starts non-blocking session analysis
- `POST /api/sessions/{session_id}/analyze/start` accepts normalized per-run options (`run_mode`, `analysis_type`, extractor toggles, sourcemap toggle, limits/failure policy) and echoes accepted config in `job.options`
- `POST /api/sessions/{session_id}/analyze/stop` requests cooperative cancellation for an active session analysis job
- `GET /api/sessions/{session_id}/analyze/progress` returns live per-file and aggregate progress (`queued`, `analyzing`, `cancelling`, `completed`, `failed`, `cancelled`) and includes active/completed run config via `job.options`
- `POST /api/sessions/bulk-delete` - Bulk session deletion with per-id success/failure reporting
- Session-level analysis aggregation
- Comprehensive session insights (`enhanced_analysis.py:281-390`)
- Session deletion now performs deterministic child-row cleanup (`dependencies`, `source_maps`, `file_analyses`, `files`) before removing session row to prevent partial-delete failures.

#### Recon Job Endpoints
- `POST /api/recon/jobs/start` - Start automated JS/map discovery and ingestion for one or more target URLs
- `GET /api/recon/jobs` - List recon jobs with latest status snapshots
- `GET /api/recon/jobs/{job_id}` - Get one job status, coverage counters, and per-asset lifecycle details
- `POST /api/recon/jobs/{job_id}/stop` - Request cooperative cancellation of an active/queued job
- Recon coverage payload is normalized to stable keys for dashboards/automation:
  - Counters: `discovered_js`, `fetched_js`, `ingested_js`, `analyzed_js`, `map_detected`, `map_fetched`, `map_failed`
  - Rates: `fetchPct`, `ingestPct`, `analysisPct`, `mapFetchPct`
  - Miss-reason taxonomy: `not_seen`, `fetch_4xx`, `fetch_5xx`, `fetch_timeout`, `non_js_content`, `blocked_by_scope`, `parse_failed`, `dedup_skipped`

### 6. Chrome Extension Integration
The application is designed to work with a Chrome extension for passive JavaScript reconnaissance:
- Supports file ingestion from browser captures
- Session-based organization of collected files
- Real-time analysis of captured JavaScript resources
- Configurable `performAnalysisOnUpload` setting (default `false`) available in Options and Popup
- Optional `importRepPlusSignals` setting to import REP+ script-like hints into dependency capture queue
- Optional `repPlusExtensionId` setting for direct cross-extension messaging with REP+
- Captured file metadata can include `repPlusSummary` for REP+ availability/count observability
- Optional auth-context capture (`captureAuthContext`) records allowlisted request auth headers and cookie presence metadata for script requests
- `authContextDomains` provides explicit per-domain controls for auth-context capture scope
- Export flow now builds payload in the service worker but performs download from popup using Blob/object URL (avoids fragile base64 data URLs for large captures)
- `exportIncludeContent` controls full-content export vs metadata-only export; oversized full-content exports now return explicit guidance instead of silent failure

## Deployment Configuration

### Docker Setup
**Production Dockerfile** (`Dockerfile`):
- Multi-stage build with Python 3.11
- Security-focused with non-root user
- External tool integration (jsluice, sourcemapper)

**Development Dockerfile** (`Dockerfile.enhanced`):
- Extended with additional development tools
- Volume mounts for hot reloading

**Docker Compose** (`docker-compose.yml`):
- PostgreSQL database with persistence
- Redis for Celery task queue (optional)
- Health checks and networking
- Environment-based configuration

### Environment Configuration
- Database connection settings
- External tool paths and configuration
- Security limits and timeouts
- CORS and API configuration
- Retention TTL controls for content artifacts: `FILE_CONTENT_TTL_DAYS` and `SOURCEMAP_CONTENT_TTL_DAYS`
- Cleanup guardrail config: `CLEANUP_MAX_DELETIONS_PER_RUN` for bounded retention runs
- Sourcemap resource limits: `SOURCEMAP_PROCESSING_TIMEOUT_SECONDS`, `SOURCEMAP_MAX_SIZE_BYTES`, `SOURCEMAP_MAX_RECONSTRUCTED_FILES`

## Development Workflow

### Testing Framework (`/tests/`)
**Comprehensive Test Suite**:
- API endpoint testing (`test_api_endpoints.py`)
- Security validation tests (`test_security_utils.py`) 
- JSluice integration tests (`test_jsluice_extractor.py`)
- File ingestion tests (`test_ingestion.py`)
- Sourcemap API DTO tests (`test_t002_sourcemap_state_dto.py`)
- Conditional ingestion analysis tests (`test_t008_conditional_ingestion_analysis.py`)
- Test fixtures and mocking (`conftest.py`)
- Extension settings smoke checks for `performAnalysisOnUpload` in Options + Popup

**Test Execution** (`run_tests.py`):
- Automated test runner with coverage
- Integration with pytest framework

### Code Organization
**Modular Structure**:
- Separation of concerns between API, models, and services
- Clean abstractions for external tool integration
- Comprehensive error handling and logging
- Security-first design principles

**Database Management**:
- SQLAlchemy ORM with relationship mapping
- Automatic schema migrations
- Query optimization and indexing

### Multi-Agent Delivery Protocol
- `TODO.md` is the centralized assignment/locking board for all tasks.
- `IMPLEMENTATION_DETAILS.md` is mandatory pre-implementation planning for each task.
- Each task requires test coverage and documented test execution.
- Each agent must run at least one other agent's relevant test suite before task completion and before claiming the next task.
- Canonical sourcemap smoke target for relevant tasks:
  - `https://finance.honeybook.com/_next/static/chunks/webpack-130dd072d1ab1095.js`
  - `https://finance.honeybook.com/_next/static/chunks/webpack-130dd072d1ab1095.js.map`
- `APPLICATION_OVERVIEW.md` must be updated when model/API/architecture/process behavior changes.

## Security Considerations

### Input Validation
- All user inputs validated through SecurityValidator
- Content size limits and format checking
- URL safety validation with dangerous pattern blocking
- Path traversal protection for file operations

### Process Isolation
- External tools run in isolated processes with timeouts
- Secure temporary file handling with cleanup
- Limited environment variables for subprocess execution
- Resource limits for memory and output size

### Data Protection
- Sensitive data sanitization in logs and responses
- Secure file deletion with data overwriting
- Session-based access control
- No credential storage or persistence

## Performance Features

### Analysis Optimization
- Background task processing for expensive operations
- Result caching and deduplication
- Batch processing capabilities
- Incremental analysis with result merging

### Resource Management
- Configurable limits for content size and processing time
- Connection pooling for database operations
- Efficient file storage with content-based deduplication
- Memory-conscious processing for large files

## Future Extension Points

### Modular Extractor System
The ComprehensiveExtractor is designed for easy extension:
- Plugin architecture for new analysis tools
- Standardized result normalization
- Configurable extractor selection

### API Extensibility
- RESTful design for easy integration
- Comprehensive error responses
- Pagination support for large datasets
- WebSocket capabilities for real-time updates

### Deployment Flexibility
- Docker-based deployment with configuration options
- Health check endpoints for monitoring
- Horizontal scaling support through stateless design
- External tool integration through configurable paths

This application represents a production-ready platform for JavaScript security analysis with comprehensive features, robust security measures, and extensive testing coverage.
