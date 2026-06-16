# INTEGRATIONS
_Last updated: 2026-04-19_

## Summary
JS Security Extractor integrates with PostgreSQL and three external CLI tools (jsluice, sourcemapper, katana) that are called via subprocess. The Chrome extension optionally integrates with the "rep+" Chrome extension via the browser's cross-extension messaging API. There are no third-party SaaS API calls from the backend — all processing is self-contained.

---

## Data Storage

**Primary Database:**
- PostgreSQL 15
  - Connection env var: `DATABASE_URL`
  - Default: `postgresql://jsextractor:changeme123@postgres:5432/js_extractor`
  - ORM client: SQLAlchemy 2.0.23 (`api/app/db.py`)
  - Schema tables: `sessions`, `files`, `source_maps`, `asset_nodes`, `asset_edges` (see `api/app/models/`)
  - Migrations: Alembic revisions at repository root; startup runs `python -m alembic upgrade head`
  - Legacy raw SQL migrations under `api/migrations/` remain historical references for earlier schema work

**File Storage:**
- Local filesystem only
  - Root path env var: `STORAGE_PATH` (default: `/var/lib/js-extractor/storage`)
  - Layout: `{STORAGE_PATH}/sessions/{session_id}/files/{content_hash}.js` and `.../maps/{content_hash}.map`
  - Managed by: `api/app/services/storage.py`
  - Docker volume: `./storage:/var/lib/js-extractor/storage` (host-mounted in `docker-compose.yml`)

**Caching / Task Queue:**
- No Redis/Celery service is part of the active supported runtime.
- Background work is API-process based and persisted in PostgreSQL job rows.

---

## External CLI Tooling (subprocess integrations)

All tool invocations are subprocess calls. Binary paths resolved by `api/app/services/binary_locator.py`.

**jsluice** (`github.com/BishopFox/jsluice`)
- Purpose: Extract URLs and secrets from JavaScript source
- Called from: `api/app/services/jsluice_extractor_secure.py`
- Invocation: `jsluice urls --unique --include-source [--resolve-paths <base_url>] <tempfile.js>`
- Binary location: `/usr/local/bin/jsluice` (default), resolvable via env var or PATH
- Fallback: Regex-based extraction if binary not found

**sourcemapper** (`github.com/denandz/sourcemapper`)
- Purpose: Reconstruct original source files from JavaScript source maps
- Called from: `api/app/services/native_sourcemap_processor.py`
- Binary location: `/usr/local/bin/sourcemapper` (default)
- Limits enforced: max source map size 50MB (`SOURCEMAP_MAX_SIZE_BYTES`), max reconstructed files 1000 (`SOURCEMAP_MAX_RECONSTRUCTED_FILES`)

**katana** (`github.com/projectdiscovery/katana`)
- Purpose: Web crawler for discovering JavaScript asset URLs
- Called from: `api/app/services/recon_job_runner.py:_discover_with_katana()`
- Output format: JSONL parsed per-line
- Binary location: `/usr/local/bin/katana` (default), or `options.katana_binary`
- Fallback: Skipped with a warning if binary not in PATH

**vespasian** (internal/custom binary)
- Purpose: Alternative JS asset discovery engine
- Called from: `api/app/services/recon_job_runner.py:_discover_with_vespasian()`
- Binary location: resolved by `binary_locator.resolve_binary_path("vespasian")` or `options.vespasian_binary`
- Timeout: `VESPASIAN_TIMEOUT_SECONDS` (default 600s)
- Not compiled in `Dockerfile.enhanced` — must be installed separately

---

## Optional / Soft Dependencies

**Playwright (Chromium headless)**
- Purpose: Intercept network responses for JS script URLs during recon
- Called from: `api/app/services/recon_job_runner.py:_discover_with_headless()`
- Import: lazy `from playwright.async_api import async_playwright` inside method
- Fallback: Returns empty set silently if import fails — recon still works without it
- Not in `requirements.txt` or `pyproject.toml` — must be installed and browsers fetched separately (`playwright install chromium`)

---

## Chrome Extension Integrations

**JS Security Extractor API (local)**
- The extension's primary integration — uploads captured JS files and source maps
- Default endpoint: `http://localhost:3000/api/save-files`
- Configurable in extension options page (`chrome-extension/options.js:42`)
- Communication: JSON POST via `fetch()` in `chrome-extension/modules/batch-uploader.js`
- Auth: No auth by default; `API_KEY` support declared in backend config but not enforced

**rep+ Chrome Extension (optional, cross-extension)**
- Purpose: Import endpoint and secret detection signals from the "rep+" security extension
- Integration: `chrome-extension/modules/rep-plus-bridge.js`
- Mechanism: `chrome.runtime.sendMessage(repPlusExtensionId, ...)` — Chrome cross-extension messaging
- Configuration: User supplies rep+ extension ID in options page (`repPlusExtensionId` setting)
- Fallback: Bridge silently no-ops if rep+ is not installed or extension ID is not set

---

## Authentication & Identity

**API Authentication:**
- No active authentication middleware on any API routes
- `api_key: str | None = None` declared in `api/app/config.py` — not currently enforced
- JWT/session auth libraries are not active route guards; `API_KEY` remains declared but unenforced

**CORS:**
- Configured in `api/app/main.py`
- Allowed origins (hardcoded): `http://localhost:3000`, `http://127.0.0.1:3000`, `http://localhost:8000`, `http://127.0.0.1:8000`
- Chrome extension origin allowed via regex: `^chrome-extension://[a-p]{32}$`

---

## Monitoring & Observability

**Error Tracking:**
- None — no Sentry, Datadog, or similar SDK

**Logging:**
- Standard Python `logging` module throughout all services
- Log level set via Uvicorn CLI flags (`--loglevel=info`)
- No structured log shipping

**Health Check:**
- HTTP: `GET /health` returns `{"status": "healthy"}`
- Docker: `HEALTHCHECK` via `curl -f http://localhost:3000/health` (in `Dockerfile.enhanced`)

---

## CI/CD & Deployment

**Hosting:**
- Docker Compose (`api/docker-compose.yml`) — primary deployment mechanism
- No cloud provider-specific configuration detected

**CI Pipeline:**
- None detected (no `.github/`, `.gitlab-ci.yml`, `Jenkinsfile`, etc.)

---

## Webhooks & Callbacks

**Incoming webhooks:** None

**Outgoing webhooks:** None — the backend fetches external URLs for JS analysis, but does not post to webhooks

---

## Kingfisher Secret Detection Rules (embedded)

The `api/app/services/rules/` directory contains ~150+ YAML rule files defining regex patterns for detecting secrets/credentials from well-known services. These rules are loaded by `api/app/services/kingfisher_rules_loader.py` entirely at runtime — no external API calls. Representative rule targets include: AWS, Azure, GCP, GitHub, Stripe, Slack, Discord, OpenAI, Anthropic, Datadog, Cloudflare, and many others.

---

## Gaps / Unknowns

- `vespasian` binary source and distribution are not documented in the repository — it is referenced but not built or fetched in any Dockerfile
- `python-jose` and `passlib` are in deps but no auth-protected routes exist; unclear if auth was removed or planned
- No `.env.example` file exists — env var documentation lives only in `api/app/config.py`
- Playwright is not in any dependency file but is used in production code paths; requires out-of-band installation
- No CI pipeline configuration found — test execution is manual
