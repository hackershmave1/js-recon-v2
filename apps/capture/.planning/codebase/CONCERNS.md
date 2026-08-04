# CONCERNS
_Last updated: 2026-04-19_

## Summary

The codebase is a security-focused JavaScript analysis tool with good foundational security hygiene (URL validation, secure subprocess wrappers, chunked regex processing). The most significant structural risks are: the complete absence of API authentication enforcement, remaining API-process job execution constraints for horizontal scaling, and partially migrated schema history that still carries legacy raw SQL context.

---

## Security Concerns

### No API Authentication Enforced

- **Risk**: All API endpoints are unauthenticated. `settings.api_key` is defined as `str | None = None` in `api/app/config.py` but is never checked in any route handler or FastAPI dependency. There are no `Depends(...)` calls using `HTTPBearer`, `APIKeyHeader`, or any custom guard in any route file.
- **Files**: `api/app/config.py:14`, `api/app/api/routes/sessions.py`, `api/app/api/routes/recon.py`, `api/app/api/routes/ingestion.py`, `api/app/api/routes/enhanced_analysis.py`
- **Impact**: Anyone with network access to the server can read all sessions, trigger analysis jobs, delete all sessions, and start recon crawls against arbitrary external hosts.
- **Fix approach**: Add a FastAPI `Security` dependency using `APIKeyHeader` that checks `settings.api_key` (when set) and apply it to all routers in `api/app/main.py`.

### `validate_url` Does Not Block Private/Internal IPs (SSRF Risk)

- **Risk**: `SecurityValidator.validate_url()` in `api/app/services/security_utils.py` explicitly allows `localhost` and raw IP addresses in its `VALID_URL_PATTERN` regex (lines 28–35). It checks `scheme` but does not block RFC-1918 addresses (`10.x`, `172.16-31.x`, `192.168.x`), link-local (`169.254.x`), or loopback. URLs accepted by the validator are passed to `httpx` in `api/app/services/http_fetcher.py`, to `asyncio.create_subprocess_exec` for katana/vespasian crawls, and to native sourcemap fetching.
- **Files**: `api/app/services/security_utils.py:28-35`, `api/app/api/routes/recon.py:317`, `api/app/api/routes/ingestion.py:409-415`, `api/app/services/http_fetcher.py`
- **Impact**: A crafted URL such as `http://169.254.169.254/latest/meta-data/` (AWS IMDS) would pass validation and be fetched by the server.
- **Fix approach**: After parsing with `urlparse`, resolve the hostname and reject RFC-1918, loopback, link-local, and metadata endpoint ranges.

### Default Credential in Config

- **Risk**: `database_url` defaults to `"postgresql://jsextractor:changeme123@localhost:5432/js_extractor"` in `api/app/config.py:5`. If the `DATABASE_URL` environment variable is not set, this password is active.
- **Files**: `api/app/config.py:5`
- **Impact**: Anyone who can reach the PostgreSQL port gets DB access with the well-known default password.
- **Fix approach**: Remove the default value so startup fails fast with a clear error if `DATABASE_URL` is not configured.

### Client-Controlled `contentHash` Used Directly in Filesystem Paths

- **Risk**: `FileIn.contentHash` is sent by the Chrome extension and stored as-is as a filename component: `{content_hash}.js` and `{content_hash}.map`. There is no server-side validation that `contentHash` is a safe hex string before it is used in `StorageService.write_file` and `write_map`.
- **Files**: `api/app/api/routes/ingestion.py:76,130,134`, `api/app/services/storage.py:21,27`
- **Impact**: A malicious `contentHash` value containing path-separator characters or shell metacharacters could cause unexpected filesystem writes. `SecurityValidator.validate_file_path` is never called on the hash.
- **Fix approach**: Add a Pydantic `field_validator` on `FileIn.contentHash` enforcing a strict `[a-fA-F0-9]{64}` pattern (SHA-256 hex), or validate server-side before passing to storage.

### Bare `except:` Clauses

- **Risk**: Three bare `except:` statements silently catch and discard all exceptions including `KeyboardInterrupt` and `SystemExit`.
- **Files**: `api/app/services/security_utils.py:336,340`, `api/app/services/parameter_extractor.py:119`
- **Impact**: Errors are swallowed, making debugging impossible and masking potential security-relevant failures.
- **Fix approach**: Replace with `except Exception:` at minimum; log the exception before continuing.

### Deprecated FastAPI Startup Event

- **Risk**: `@app.on_event("startup")` is used in `api/app/main.py:43`. This decorator was deprecated in FastAPI 0.93 in favour of the `lifespan` context manager.
- **Files**: `api/app/main.py:43`
- **Impact**: Will be removed in a future FastAPI version; no immediate security risk but signals dependency lag.

---

## Technical Debt

### Ad-hoc Runtime Schema Migrations Instead of Alembic

- **Issue**: `ensure_runtime_schema_updates()` in `api/app/main.py` (lines 93–156) performs 20+ `ALTER TABLE` and `UPDATE` statements as raw SQL on every app startup. Alembic is listed as a dependency and three `.sql` migration files exist in `api/migrations/`, but they are never applied by the application — Alembic's migration runner is not wired in anywhere.
- **Files**: `api/app/main.py:93-156`, `api/migrations/`
- **Impact**: Startup time penalty on every boot; logic is fragile when run concurrently (no advisory lock); incompatible with multi-replica deployments; Alembic history is out of sync with the actual schema.
- **Fix approach**: Convert the startup mutations into proper Alembic migration files and invoke `alembic upgrade head` as part of the deployment step rather than at runtime.

### API-Process Job Execution Still Limits Horizontal Scaling

- **Issue**: Job state is persisted in PostgreSQL and startup recovery marks orphaned active rows terminal, but actual work still runs in API-process background threads/events.
- **Files**: `api/app/api/routes/sessions.py`, `api/app/api/routes/recon.py`, `api/app/services/job_recovery.py`
- **Impact**: A restart no longer leaves old jobs `running`, but multiple API replicas still need explicit worker ownership/locking before they are safe for production horizontal scaling.
- **Fix approach**: Add owner/heartbeat fields and claim semantics, or move job execution into a single supported worker system with documented ownership guarantees.

### Legacy Celery References Were Removed From Active Runtime

- **Issue**: The active imports, Docker services, and docs no longer require Celery/Redis. Watch for old references in historical archives only.
- **Files**: `api/app/tasks/`, `api/docker-compose.yml`, `api/README.md`, `.planning/codebase/*`
- **Impact**: Misleading references can cause developers to attempt unsupported worker startup.
- **Fix approach**: Keep historical references out of active docs; if scheduled cleanup is required later, add a documented non-Celery command or restore a supported worker intentionally.

### `python-jose` and `passlib` Are Unused

- **Issue**: `python-jose[cryptography]==3.3.0` and `passlib[bcrypt]==1.7.4` are listed as production dependencies but are imported nowhere in `api/app/`. These are JWT and password-hashing libraries, suggesting an authentication system was planned but never implemented (consistent with the missing auth guards above).
- **Files**: `api/requirements.txt:12-13`, `api/pyproject.toml`
- **Impact**: Extra install surface area, including cryptography primitives; `python-jose` has had CVEs related to algorithm confusion attacks.
- **Fix approach**: Remove from requirements unless auth is being added. If auth is planned, prefer `PyJWT` + `bcrypt` directly over `python-jose`.

### Large Route Files with Mixed Concerns

- **Issue**: `api/app/api/routes/sessions.py` is 1,320 lines and combines HTTP routing, job orchestration, background thread management, data aggregation, and statistical computation. `api/app/api/routes/ingestion.py` is 1,013 lines with similar mixing.
- **Files**: `api/app/api/routes/sessions.py`, `api/app/api/routes/ingestion.py`
- **Impact**: High cognitive load; difficult to test individual concerns; functions like `execute_session_analysis` (line 822) and `compute_global_stats` (line 1279) belong in service classes.
- **Fix approach**: Extract job orchestration into `api/app/services/session_analysis_service.py`, stats into `api/app/services/stats_service.py`.

### `dashboard.js` Is 4,295 Lines with No Modularisation

- **Issue**: `api/app/static/dashboard.js` is a single 4,295-line file containing all frontend logic for the dashboard, with no module system.
- **Files**: `api/app/static/dashboard.js`
- **Impact**: Unmaintainable; any change risks regressions; no tree-shaking possible; the only frontend test (`test_t023_dashboard_failure_utils.mjs`) covers a small extracted utility file, leaving the bulk untested.

---

## Missing / Incomplete

### Endpoint Deduplication / Rollup Is Hardcoded to Zero

- **What's missing**: `GET /api/sessions/{session_id}/analysis/summary` returns `"total_unique_endpoints": 0` hardcoded with a `# TODO: Implement endpoint rollup in future task` comment. A stub `summarize_endpoint_rollup` function exists at line 1258 but is not called.
- **Files**: `api/app/api/routes/sessions.py:1214`, `api/app/api/routes/sessions.py:1258`
- **Impact**: The analysis summary endpoint cannot be used to deduplicate endpoints across files; any UI or downstream consumer receives 0 for this field.

### No API Authentication Layer

- **What's missing**: No middleware, dependency, or route guard enforces the `api_key` setting. See Security Concerns above.

### No Rate Limiting

- **What's missing**: No rate limiting middleware (`slowapi`, custom, or otherwise) is configured. The recon endpoint can trigger multiple external crawls. The ingestion endpoint accepts arbitrarily large batches.
- **Files**: `api/app/main.py`

### No Input Size Limit on Uploaded File Content

- **What's missing**: `SecurityValidator.validate_js_content` enforces a 10 MB limit per file, but the FastAPI ingestion route accepts `FileIn` payloads via JSON body without a server-level `Content-Length` cap. A batch of files each under 10 MB can still produce very large requests.
- **Files**: `api/app/api/routes/ingestion.py:96-140`

---

## Dependency Concerns

| Package | Version Pinned | Concern |
|---|---|---|
| `fastapi` | `0.104.1` | Over 18 months behind current (0.115+); `@app.on_event` deprecated |
| `python-jose` | `3.3.0` | Known algorithm confusion CVE (CVE-2024-33664); not actively used — remove |
| `passlib` | `1.7.4` | Last release 2022; `bcrypt` backend has a known silent truncation bug at 72 bytes; not used — remove |
| `httpx` | `0.25.2` | Multiple releases behind; later versions improved SSRF-related redirect behaviour |
| `pydantic` | `2.5.0` | ~1.5 years behind 2.10+ |
| `uvicorn` | `0.24.0` | Behind current; security-relevant fixes in 0.27+ |

---

## TODO / FIXME Inventory

**Count in project source (excluding `.venv` and `node_modules`):** 1 TODO

| Location | Comment |
|---|---|
| `api/app/api/routes/sessions.py:1214` | `# TODO: Implement endpoint rollup in future task` |

No `FIXME`, `HACK`, or `XXX` markers exist in the project source files.

---

## Gaps / Unknowns

- **Chrome extension security posture not fully audited**: `chrome-extension/background.js` (999 lines) handles auth header capture and proxies requests to the backend. It captures `authorization`, `x-auth-token`, `x-csrf-token` headers from browser requests and sends them to the local API. The risk surface of this data path (stored plaintext in the DB as `authContext`) was not deeply audited.
- **Kingfisher rules not inspected**: `api/app/services/kingfisher_rules_loader.py` loads YAML rules from disk at runtime. The rules directory and their content were not reviewed for injection or path traversal risks in the loader.
- **No `alembic.ini` or `env.py` found**: The `api/migrations/` directory contains only raw `.sql` files, not Alembic revision scripts. It is unclear whether Alembic has ever been run against this schema.
- **External binary availability not verified**: The application depends on `jsluice` at `/usr/local/bin/jsluice`, `sourcemapper` (path configurable), `katana` (resolved via `PATH`), and `vespasian` (resolved via `PATH`). None of these are checked at startup; failures surface only at first use. `binary_locator.py` exists but its integration was not traced.
- **SSRF test coverage absent**: No test in `api/tests/` exercises private-IP or localhost URLs against `validate_url`; the SSRF risk noted above is untested.
