# STRUCTURE
_Last updated: 2026-04-19_

## Summary

The repository is split into three top-level areas: `api/` (Python FastAPI backend), `chrome-extension/` (MV3 browser extension), and root-level documentation/scripts. All production Python code lives under `api/app/` following a routes/services/models layering. Tests are co-located under `api/tests/`. Static frontend assets are served directly from `api/app/static/` and `api/app/templates/`.

---

## Directory Layout

```
js-security-extractor/
├── api/                          # Python backend (FastAPI + PostgreSQL)
│   ├── app/                      # Application package
│   │   ├── main.py               # FastAPI app factory, startup hook, router wiring
│   │   ├── config.py             # Settings via pydantic-settings (env vars)
│   │   ├── db.py                 # SQLAlchemy engine, SessionLocal, get_db()
│   │   ├── __init__.py
│   │   ├── api/
│   │   │   └── routes/           # HTTP route handlers (one file per domain)
│   │   │       ├── ingestion.py  # POST /api/save-files
│   │   │       ├── sessions.py   # Session CRUD + bulk analyze
│   │   │       ├── files.py      # Single-file operations
│   │   │       ├── recon.py      # Recon job lifecycle
│   │   │       ├── enhanced_analysis.py  # Standalone analysis endpoints
│   │   │       ├── asset_graph.py        # Asset graph queries
│   │   │       ├── dashboard.py  # HTML dashboard route
│   │   │       └── __init__.py
│   │   ├── models/               # SQLAlchemy ORM models
│   │   │   ├── __init__.py       # Re-exports all models (import here for create_all)
│   │   │   ├── session.py        # Session model
│   │   │   ├── file.py           # File model
│   │   │   ├── file_analysis.py  # FileAnalysis model
│   │   │   ├── source_map.py     # SourceMap model
│   │   │   ├── dependency.py     # Dependency model
│   │   │   └── asset_graph.py    # AssetNode, AssetEdge, enums
│   │   ├── services/             # Business logic layer
│   │   │   ├── comprehensive_extractor.py  # Main analysis orchestrator
│   │   │   ├── recon_job_runner.py         # Autonomous JS discovery runner
│   │   │   ├── jsluice_extractor_secure.py # Canonical hardened jsluice wrapper
│   │   │   ├── rep_endpoints_extractor.py  # Regex endpoint extraction
│   │   │   ├── rep_secrets_extractor.py    # Kingfisher-rule secret extraction
│   │   │   ├── native_sourcemap_processor.py  # Sourcemap parse/reconstruct
│   │   │   ├── sourcemap_processor.py      # Compatibility alias for native processor
│   │   │   ├── sourcemap_validation.py     # Sourcemap fetch/state tracking
│   │   │   ├── parameter_extractor.py      # URL/function parameter extraction
│   │   │   ├── sensitive_file_detector.py  # Sensitive file reference detection
│   │   │   ├── endpoint_sanitizer.py       # Endpoint post-processing/dedup
│   │   │   ├── secret_rollup.py            # Secret dedup with provenance
│   │   │   ├── asset_graph_service.py      # Asset DAG CRUD
│   │   │   ├── analysis_compactor.py       # Result payload truncation
│   │   │   ├── analysis_triggers.py        # Auto-analysis heuristics
│   │   │   ├── file_priority.py            # File importance ranking
│   │   │   ├── auth_context.py             # Auth header capture/sanitize
│   │   │   ├── http_fetcher.py             # robust_fetcher() with retries
│   │   │   ├── security_utils.py           # Input validation, shell safety
│   │   │   ├── binary_locator.py           # External binary path resolution
│   │   │   ├── retention_cleanup.py        # TTL-based content purge
│   │   │   ├── job_recovery.py             # Startup recovery for orphaned jobs
│   │   │   ├── kingfisher_rules_loader.py  # Loads YAML rules from rules/
│   │   │   ├── storage.py                  # StorageService: disk write helpers
│   │   │   ├── async_utils.py              # run_coroutine_sync() bridge
│   │   │   ├── regex_utils.py              # Chunked regex helpers
│   │   │   └── rules/                      # Kingfisher YAML secret-detection rules
│   │   │       ├── aws.yaml
│   │   │       ├── anthropic.yaml
│   │   │       └── ... (200+ provider YAML files)
│   │   ├── static/               # Served at /static
│   │   │   ├── dashboard.js      # SPA frontend (~4300 lines, vanilla JS)
│   │   │   ├── dashboard.css     # Dashboard styles
│   │   │   └── dashboard-failure-utils.js  # Failure state helpers
│   │   └── templates/
│   │       └── dashboard.html    # Jinja2 base template for the SPA
│   ├── migrations/               # Raw SQL migration scripts
│   │   ├── 20260208_001_add_sourcemap_processing_state.sql
│   │   ├── 20260209_002_add_file_session_hash_unique.sql
│   │   └── 20260211_003_add_asset_graph_tables.sql
│   ├── storage/                  # Runtime file storage (gitignored content)
│   │   └── sessions/
│   │       └── {session_uuid}/
│   │           ├── files/        # {content_hash}.js
│   │           └── maps/         # {content_hash}.map
│   ├── tests/                    # Pytest test suite
│   │   ├── conftest.py
│   │   ├── test_api_endpoints.py
│   │   ├── test_b*.py            # Feature/backlog tests (numbered B-NNN)
│   │   └── test_t*.py            # Task/ticket tests (numbered T-NNN)
│   ├── Dockerfile
│   ├── Dockerfile.enhanced
│   ├── docker-compose.yml
│   ├── pyproject.toml            # Dependencies (uv-managed)
│   ├── uv.lock
│   └── requirements.txt          # Legacy requirements (superseded by pyproject.toml)
├── chrome-extension/             # Browser extension (MV3)
│   ├── manifest.json             # Extension manifest (permissions, entry points)
│   ├── background.js             # Service worker: JSExtractor orchestrator
│   ├── content-script.js         # Injected page script
│   ├── popup.html / popup.js     # Extension popup UI
│   ├── options.html / options.js # Extension settings page
│   ├── modules/                  # ES module imports for background.js
│   │   ├── batch-uploader.js     # HTTP upload batching to /api/save-files
│   │   ├── content-fetcher.js    # JS body fetch
│   │   ├── decompressor.js       # Response decompression
│   │   ├── dependency-extractor.js  # Import/script-tag parsing
│   │   ├── enhanced-analyzer.js  # In-extension lightweight analysis
│   │   ├── export-builder.js     # Local export builder
│   │   ├── rep-plus-bridge.js    # Bridge to REP+ extension
│   │   └── sourcemap-detector.js # SourceMap URL/header detection
│   ├── lib/                      # Vendored JS libraries
│   ├── icons/                    # Extension icons (16/48/128px)
│   └── tests/                    # Extension JS tests
├── scripts/                      # Dev/ops utility scripts
├── docs/                         # Additional documentation
├── tests/                        # Root-level integration tests
├── tmp/                          # Scratch/temporary files
├── .planning/                    # GSD planning artifacts
│   └── codebase/                 # Codebase map documents
├── README.md
├── APPLICATION_OVERVIEW.md       # High-level feature description
├── TODO.md                       # Active development backlog
├── IMPLEMENTATION_DETAILS.md     # Deep implementation notes
└── validate_setup.sh             # Environment validation script
```

---

## Key File Locations

**Entry Points:**
- `api/app/main.py` — FastAPI app creation, router registration, startup hook
- `chrome-extension/background.js` — Extension service worker (main logic)
- `chrome-extension/manifest.json` — Extension entry point declarations

**Configuration:**
- `api/app/config.py` — All settings via `pydantic-settings`; reads env vars without prefix
- `api/docker-compose.yml` — Dev environment: postgres and api
- `api/pyproject.toml` — Python dependencies (managed with `uv`)

**Core Logic:**
- `api/app/services/comprehensive_extractor.py` — Central analysis orchestrator
- `api/app/services/recon_job_runner.py` — Autonomous JS asset discovery
- `api/app/api/routes/ingestion.py` — File ingest + sourcemap pipeline
- `api/app/api/routes/sessions.py` — Session management + bulk analysis

**Data Models:**
- `api/app/models/__init__.py` — All ORM model exports (must import for `create_all` to work)
- `api/app/db.py` — `get_db()` dependency, `Base`, `engine`

**Frontend:**
- `api/app/static/dashboard.js` — SPA (~4300 lines, all vanilla JS)
- `api/app/templates/dashboard.html` — HTML shell served by `dashboard.py` route

**Tests:**
- `api/tests/` — All pytest tests; named `test_b{NNN}_*.py` (backlog) or `test_t{NNN}_*.py` (task)

---

## Naming Conventions

**Python files:**
- `snake_case.py` for all service, model, route files
- Route files named after their domain noun: `sessions.py`, `files.py`, `ingestion.py`, `recon.py`
- Service files named after their function: `comprehensive_extractor.py`, `secret_rollup.py`
- Test files: `test_b{NNN}_{description}.py` (backlog feature) or `test_t{NNN}_{description}.py` (ticket task)

**Classes:**
- `PascalCase` for all classes
- ORM models aliased on import to avoid collision with SQLAlchemy `Session`: `from ...models import Session as DbSession`

**JavaScript files:**
- `kebab-case.js` for extension modules: `batch-uploader.js`, `content-fetcher.js`
- `camelCase` for class names: `JSExtractor`, `BatchUploader`, `ContentFetcher`

**Storage paths:**
- `{storage_path}/sessions/{session_uuid}/files/{content_hash}.js`
- `{storage_path}/sessions/{session_uuid}/maps/{content_hash}.map`

**Migration files:**
- `YYYYMMDD_NNN_description.sql` — date-prefixed sequential SQL migrations

---

## Where to Add New Code

**New API endpoint:**
- Create/extend a file in `api/app/api/routes/`
- Register the router in `api/app/main.py` with `app.include_router(...)`
- Use `db: Session = Depends(get_db)` for DB access

**New extraction/analysis logic:**
- Add a service class in `api/app/services/`
- Integrate it into `ComprehensiveExtractor.__init__()` and `extract_all()` in `api/app/services/comprehensive_extractor.py`

**New ORM model:**
- Create `api/app/models/{name}.py`
- Add to `api/app/models/__init__.py` for `create_all` registration

**New secret detection rules:**
- Add a `{provider}.yaml` YAML file to `api/app/services/rules/` following Kingfisher format
- Rules auto-loaded by `kingfisher_rules_loader.py`

**New test:**
- Add `api/tests/test_b{NNN}_{feature}.py` for backlog items or `test_t{NNN}_{task}.py` for tasks
- Use `conftest.py` fixtures in `api/tests/conftest.py`

**Extension feature:**
- New capability modules go in `chrome-extension/modules/`
- Import into `chrome-extension/background.js` using ES module syntax

---

## Special Directories

**`api/storage/sessions/`:**
- Purpose: Persisted JS file content and source maps keyed by session UUID + content hash
- Generated: Yes (at runtime by `StorageService`)
- Committed: No (runtime data; sample session present for testing)

**`api/app/services/rules/`:**
- Purpose: Kingfisher YAML rule files for secret pattern detection (200+ provider files)
- Generated: No (curated rule library)
- Committed: Yes

**`api/.venv/`:**
- Purpose: Python virtual environment managed by `uv`
- Generated: Yes
- Committed: No

**`.planning/codebase/`:**
- Purpose: GSD codebase map artifacts consumed by planning/execution agents
- Generated: Yes (by mapper agent)
- Committed: Yes

---

## Gaps / Unknowns

- `api/tests/` has both `test_t023_dashboard_failure_utils.mjs` (ES module test) and `analyze_wishandwash.py` / `check_todo_hygiene.sh` — these appear to be utility scripts mixed into the test directory
- `chrome-extension/lib/` contents not inspected — likely vendored dependencies
- `scripts/` and `docs/` directories not fully explored
- `api/app/services/native_sourcemap_processor.py` is the canonical sourcemap processor; `sourcemap_processor.py` is retained only as a compatibility alias.
