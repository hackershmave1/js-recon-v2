# ARCHITECTURE
_Last updated: 2026-04-19_

## Summary

JS Security Extractor is a security-focused JavaScript reconnaissance tool composed of two primary components: a Chrome extension that intercepts network traffic and uploads JS files, and a FastAPI backend that stores, analyzes, and presents security findings. The backend follows a layered monolith pattern (routes -> services -> models/DB). Background work is handled by FastAPI background tasks, background threads for long job runners, and DB-backed job records. A headless recon subsystem can autonomously discover and ingest JavaScript assets from target URLs using multiple discovery engines (Playwright headless, Katana, Vespasian).

---

## Overall Pattern

**Layered monolith** with API-process background work and persistent job records.

```
Chrome Extension  ──POST /api/save-files──►  FastAPI API Layer
                                                    │
                  ──POST /api/recon/jobs/start──►   │
                                                    ▼
                                            Route Handlers
                                                    │
                                            Service Layer
                                           (extractors, etc.)
                                                    │
                                  ┌─────────────────┼──────────────────┐
                                  ▼                 ▼                  ▼
                            PostgreSQL         File Storage        Job Recovery
                           (SQLAlchemy)       (local disk)         (startup)
```

---

## Layers

### HTTP / Route Layer

- Purpose: Accept HTTP requests, validate Pydantic models, call services, return JSON
- Location: `api/app/api/routes/`
- Files:
  - `ingestion.py` — `POST /api/save-files`: accepts bulk JS file payloads from the extension, persists to DB + disk, optionally triggers sourcemap fetch + analysis
  - `sessions.py` — session listing, bulk re-analysis (`POST /api/sessions/{id}/analyze`), stats, rename, delete
  - `files.py` — single-file operations: retrieve content, re-analyze, delete
  - `recon.py` — recon job lifecycle: start, status poll, stop (`/api/recon/jobs/*`)
  - `enhanced_analysis.py` — standalone analysis endpoints (`/api/analyze-comprehensive`, `/api/analyze-by-url`)
  - `asset_graph.py` — graph queries for a session's asset discovery provenance (`/api/sessions/{id}/asset-graph/*`)
  - `dashboard.py` — serves `dashboard.html` at `/`, `/dashboard`, `/sessions`, `/analysis`
- Depends on: Service layer, `db.get_db()` via FastAPI `Depends()`
- Pattern: All routes use `db: Session = Depends(get_db)` for SQLAlchemy sessions injected per-request

### Service Layer

- Purpose: Business logic, external tool invocation, extraction algorithms
- Location: `api/app/services/`
- Key services:

| File | Purpose |
|------|---------|
| `comprehensive_extractor.py` | Orchestrates all extractors (REP, jsluice, sourcemap, parameter) into a single `extract_all()` result |
| `recon_job_runner.py` | Drives headless/Katana/Vespasian discovery, fetches JS assets, feeds ingestion pipeline |
| `jsluice_extractor_secure.py` | Canonical hardened wrapper that shells out to `jsluice` for URL and secret extraction |
| `rep_endpoints_extractor.py` | Regex-pattern endpoint extraction (REP-style) |
| `rep_secrets_extractor.py` | Regex-pattern secret extraction using Kingfisher YAML rules |
| `native_sourcemap_processor.py` | Parses and reconstructs sources from `.map` files |
| `sourcemap_processor.py` | Compatibility alias for `native_sourcemap_processor.py` |
| `job_recovery.py` | Startup recovery for orphaned queued/running/cancelling DB jobs |
| `sourcemap_validation.py` | Builds validation state objects tracking sourcemap fetch/parse outcomes |
| `parameter_extractor.py` | Extracts URL and function parameters from JS |
| `sensitive_file_detector.py` | Identifies references to sensitive files in JS content |
| `endpoint_sanitizer.py` | Post-processes extracted endpoints to remove noise/false positives |
| `secret_rollup.py` | Deduplicates secrets across files by type+value with provenance (B-025) |
| `asset_graph_service.py` | Creates/queries the asset discovery DAG (B-027) |
| `analysis_compactor.py` | Truncates/limits analysis result payload for DB storage |
| `analysis_triggers.py` | Heuristics deciding whether a file warrants auto-analysis |
| `file_priority.py` | Ranks JS files by analysis importance (entrypoint vs vendor vs analytics) |
| `auth_context.py` | Captures and sanitizes auth headers for sourcemap fetch replay |
| `http_fetcher.py` | `robust_fetcher()` with retry logic, size caps, timeout hardening |
| `security_utils.py` | `SecurityValidator` class for URL, content, and command-injection validation |
| `binary_locator.py` | Resolves paths to external binaries (jsluice, katana, vespasian) |
| `retention_cleanup.py` | TTL-based file content purging (marks `content_purged=true`, deletes disk content) |
| `kingfisher_rules_loader.py` | Loads YAML secret-detection rules from `services/rules/` |
| `storage.py` | `StorageService`: wraps disk writes for JS files and sourcemaps |
| `async_utils.py` | `run_coroutine_sync()` bridge for calling async code from sync contexts |

- Depends on: Models layer, `config.settings`, external binaries (`jsluice`, `katana`, `vespasian`)

### Models / Data Layer

- Purpose: SQLAlchemy ORM definitions, DB schema
- Location: `api/app/models/`
- Files:
  - `session.py` — `Session`: UUID PK, `name`, `source`, `version`; has `files`, `asset_nodes`, `asset_edges`
  - `file.py` — `File`: UUID PK, FK to `sessions`, `url`, `content_hash`, `stored_path`, `content_purged` TTL fields
  - `source_map.py` — `SourceMap`: 1:1 to `File`, tracks processing status, validation state, content purge
  - `file_analysis.py` — `FileAnalysis`: 1:1 to `File`, stores JSON analysis result
  - `dependency.py` — `Dependency`: N:1 to `File`, tracks JS imports
  - `asset_graph.py` — `AssetNode` + `AssetEdge`: DAG nodes/edges with discovery method enums

### Database

- Engine: PostgreSQL 15 (primary target); SQLite also accepted (dialect-conditional SQL in `main.py`)
- ORM: SQLAlchemy 2.0 with `SessionLocal` / `get_db()` generator
- Schema management: Alembic revisions at repository root; startup locates `alembic.ini` and runs `python -m alembic upgrade head`
- JSONB columns used on PostgreSQL for `file_metadata`, `validation_state`, `asset_metadata`

### Background Work Layer

- Purpose: Keep long work observable without requiring Celery/Redis.
- Session analysis and recon jobs persist status in the `jobs` table and execute in API-process background threads.
- Batch analysis endpoints use FastAPI `BackgroundTasks`.
- Startup runs `services/job_recovery.py` after Alembic migrations and marks orphaned `queued`, `running`, or `cancelling` jobs as `failed` or `cancelled`.
- TTL cleanup is implemented by `services/retention_cleanup.py`; it is no longer scheduled by Celery Beat in the supported Compose stack.

### Dashboard / Frontend

- Location: `api/app/templates/dashboard.html`, `api/app/static/dashboard.js`, `api/app/static/dashboard.css`
- Served as: Jinja2 template at `/` from FastAPI
- Architecture: Single-page vanilla JS (~4300 lines) consuming the backend REST API; no framework
- Static files mounted at `/static`

### Chrome Extension

- Location: `chrome-extension/`
- Entry points: `background.js` (service worker), `content-script.js`, `popup.html/js`, `options.html/js`
- Pattern: MV3 service worker with module imports
- Modules in `chrome-extension/modules/`:
  - `content-fetcher.js` — fetches JS body via `fetch()`
  - `dependency-extractor.js` — parses script tags and JS imports
  - `sourcemap-detector.js` — detects `sourceMappingURL` comments and `SourceMap` headers
  - `decompressor.js` — decompresses gzip/brotli responses
  - `batch-uploader.js` — batches files and POSTs to `POST /api/save-files`
  - `rep-plus-bridge.js` — optional bridge to REP+ extension
  - `export-builder.js` — builds local export payloads
  - `enhanced-analyzer.js` — lightweight in-extension analysis

---

## Data Flow

### Extension Capture Flow

1. Browser loads a page; `webRequest` listener intercepts JS responses
2. `background.js` collects response body, headers, auth context, sourcemap URL
3. `BatchUploader` queues files; periodically POSTs `IngestionPayload` to `POST /api/save-files`
4. `ingestion.py:save_files()` deduplicates by `(session_id, content_hash)`, persists `File` + `SourceMap` rows, writes content to `StorageService`
5. If sourcemap URL detected: fetcher attempts to download map, stores to disk, updates `SourceMap.processing_status`
6. `SmartAnalysisTriggers` evaluates content heuristics; if triggered, `ComprehensiveExtractor.extract_all()` runs synchronously and result stored in `FileAnalysis`

### Recon Flow

1. User POSTs `POST /api/recon/jobs/start` with target URLs and discovery engine choice
2. `recon.py` creates a session, spawns `ReconJobRunner` in a background thread
3. `ReconJobRunner._discover_target()` routes to headless Playwright / katana / vespasian binary
4. Discovered JS URLs are fetched via `robust_fetcher()`, deduplicated, batched
5. Batches are passed directly to `save_files()` (bypassing HTTP), re-using ingestion pipeline
6. Job status polled via `GET /api/recon/jobs/{id}`

### Session Analysis Flow

1. User triggers `POST /api/sessions/{id}/analyze`
2. `sessions.py` spawns a `threading.Thread` (not Celery), running `SessionAnalysisJob`
3. Files are processed in batches; `ComprehensiveExtractor.extract_all()` runs per file
4. Results stored in `FileAnalysis`; progress tracked in in-memory `SESSION_ANALYSIS_JOBS` dict

---

## Error Handling

**Strategy:** Per-layer exception handling, with `continue_on_error` flags for batch operations.

**Patterns:**
- Route handlers raise `HTTPException` for 4xx/5xx responses
- `ComprehensiveExtractor.extract_all()` catches exceptions per extractor and continues
- `ReconJobRunner` logs and classifies errors by taxonomy (`not_seen`, `fetch_4xx`, `fetch_5xx`, etc.)
- Sourcemap failures recorded in `SourceMap.processing_error` field
- Auth context replay: 401/403/429 errors on sourcemap fetch trigger credential replay

---

## External Binary Dependencies

Three optional external tools invoked via subprocess:

| Binary | Purpose | Location strategy |
|--------|---------|-------------------|
| `jsluice` | JS URL/secret extraction | Resolved via `binary_locator.resolve_binary_path()` |
| `katana` | Web crawling discovery | Same binary locator |
| `vespasian` | Alternate crawler | Same binary locator |

Search order: env var → `PATH` → `api/.tools/bin/` → `~/.local/bin/` → `/usr/local/bin/` → `/usr/bin/`

---

## Cross-Cutting Concerns

**Security validation:** `SecurityValidator` in `services/security_utils.py` validates URLs (max 2048 chars), JS content (max 10MB), blocks shell metacharacters before subprocess calls.

**Content TTL/retention:** `retention_cleanup.py` purges disk content (sets `content_purged=true`, deletes file) after `file_content_ttl_days` / `sourcemap_content_ttl_days`. No Celery Beat scheduler is part of the active runtime.

**CORS:** Allows `localhost:3000/8000` and Chrome extension origins (`chrome-extension://[a-p]{32}`).

**Auth context:** Extension captures auth headers (allowlisted: `authorization`, `cookie`, `x-api-key`, etc.) and passes them in `FileIn.authContext` for sourcemap fetch replay.

**Chunked regex:** Files > 1MB are split into 100KB chunks with 5KB overlap for regex extraction to avoid ReDoS and memory issues.

---

## Gaps / Unknowns

- Long-running jobs are persisted, but API-process execution means multi-replica deployment still needs explicit job ownership/locking before use.
- `api/app/services/rules/` contains hundreds of YAML files (Kingfisher secret rules) — exact load/merge behavior not fully traced
- No API authentication on the backend endpoints (the `api_key` config setting exists but is not enforced in any route)
