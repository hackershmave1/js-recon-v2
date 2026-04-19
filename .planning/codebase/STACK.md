# STACK
_Last updated: 2026-04-19_

## Summary
JS Security Extractor is a security reconnaissance tool with two main components: a Python/FastAPI backend API and a Chrome extension (Manifest V3). The backend orchestrates JavaScript extraction, source map reconstruction, secret detection, and asset graph building. The Chrome extension captures in-browser JS traffic and uploads it to the local API.

---

## Languages

**Primary:**
- Python 3.11 — entire backend API, all services and task workers
- JavaScript (ES Modules) — Chrome extension (no transpile step, native browser ESM)

**Secondary:**
- Go 1.21.5 — used at Docker build time to compile external analysis binaries (not runtime Go in the app itself)
- SQL — raw migration scripts in `api/migrations/`

---

## Runtime

**Environment:**
- Python 3.11 (enforced in `pyproject.toml` via `requires-python = ">=3.11"`)
- Docker base image: `python:3.11-slim`

**Package Manager:**
- `uv` — used in `Dockerfile.enhanced` and `docker-compose.yml` (`uv run`, `uv sync --frozen`)
- `pip` — used in the simpler `Dockerfile` (no uv)
- Lockfile: `api/uv.lock` (present and committed)
- Legacy: `api/requirements.txt` (mirrors `pyproject.toml` deps, used by simple `Dockerfile`)

---

## Frameworks

**Core:**
- FastAPI 0.104.1 — REST API server (`api/app/main.py`)
- Uvicorn 0.24.0 (standard extras) — ASGI server, port 3000
- Jinja2 3.1.2 — server-side HTML rendering for the dashboard (`api/app/api/routes/dashboard.py`, `api/app/templates/dashboard.html`)

**Data / ORM:**
- SQLAlchemy 2.0.23 — ORM and raw SQL execution (`api/app/db.py`)
- Alembic 1.13.0 — database migrations (configured in `api/migrations/`)
- Pydantic 2.5.0 — request/response validation models throughout routes
- pydantic-settings 2.1.0 — settings loaded from env vars (`api/app/config.py`)

**Async Task Queue:**
- Celery 5.3.4 — background task worker (`api/app/tasks/celery_app.py`)
  - Three services: `celery_worker`, `celery_beat` (scheduled tasks), `api`
  - Beat schedule: `retention_cleanup` task runs daily at 03:00 UTC

**Testing:**
- pytest 7.4.3
- pytest-asyncio >= 0.23.8 — async test support
- pytest-cov >= 7.0.0 — coverage reporting

---

## Key Dependencies

**HTTP Client:**
- httpx 0.25.2 — async HTTP fetching in `api/app/services/http_fetcher.py` and `api/app/api/routes/ingestion.py`

**File I/O:**
- aiofiles 23.2.1 — listed in requirements; storage writes currently use synchronous `Path.write_text` in `api/app/services/storage.py`

**Data Parsing:**
- PyYAML 6.0.3 — loads Kingfisher secret-detection rules from `api/app/services/rules/*.yaml` (`api/app/services/kingfisher_rules_loader.py`)
- python-multipart 0.0.6 — multipart form uploads for file ingestion

**Security / Auth (declared, not actively wired to routes):**
- python-jose[cryptography] 3.3.0 — JWT library (in requirements; no active JWT middleware found in routes)
- passlib[bcrypt] 1.7.4 — password hashing (in requirements; no active auth routes found)

**Optional / Runtime-detected:**
- playwright (Chromium) — imported lazily in `api/app/services/recon_job_runner.py:482`; falls back gracefully if not installed. Not in `requirements.txt` — must be installed separately when headless discovery is needed.

---

## External Go Binaries (compiled at Docker build time)

These are not Python packages. They are compiled from source during `docker build` using `Dockerfile.enhanced` and placed at `/usr/local/bin/`:

| Binary | Source | Purpose |
|--------|--------|---------|
| `jsluice` | `github.com/BishopFox/jsluice` | JS URL and secret extraction via subprocess |
| `sourcemapper` | `github.com/denandz/sourcemapper` | Source map reconstruction via subprocess |
| `katana` | `github.com/projectdiscovery/katana` | Web crawler for JS asset discovery |
| `vespasian` | Internal / referenced by name | Alternative JS discovery engine (binary resolved by `api/app/services/binary_locator.py`) |

Binary paths are resolved at runtime via `api/app/services/binary_locator.py`, checking env vars, `PATH`, `.tools/bin/`, `~/.local/bin/`, `/usr/local/bin/`, `/usr/bin/` in priority order.

---

## Configuration

**Environment variables (from `api/app/config.py`):**

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | `postgresql://jsextractor:changeme123@localhost:5432/js_extractor` | PostgreSQL connection |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection (Celery broker + backend) |
| `STORAGE_PATH` | `/var/lib/js-extractor/storage` | Local file storage root |
| `API_KEY` | `None` | Optional API key (declared, not enforced in routes) |
| `FILE_CONTENT_TTL_DAYS` | `30` | Retention for JS file content |
| `SOURCEMAP_CONTENT_TTL_DAYS` | `30` | Retention for source map content |
| `FETCH_TIMEOUT_SECONDS` | `30` | HTTP fetch timeout |
| `REGEX_CHUNK_SIZE_THRESHOLD` | `1MB` | Chunked regex threshold |
| `SMART_ANALYSIS_ENABLED` | `true` | Enable heuristic analysis triggers |

No `.env` file found in the repository root.

**Build:**
- `api/pyproject.toml` — primary Python project definition
- `api/uv.lock` — locked dependency graph
- `api/requirements.txt` — legacy pip-compatible list (used by simple `Dockerfile`)
- `api/Dockerfile` — pip-based image (no binary tools)
- `api/Dockerfile.enhanced` — uv-based image with Go binaries compiled in
- `api/docker-compose.yml` — full stack: postgres, redis, api, celery_worker, celery_beat

---

## Platform Requirements

**Development:**
- Python >= 3.11
- `uv` package manager recommended
- PostgreSQL 15 and Redis 7 (via Docker Compose)
- Optional: Go 1.21.5+ if building binaries locally
- Optional: Playwright + Chromium for headless JS discovery

**Production / Container:**
- `Dockerfile.enhanced` is the production image
- All Go binaries compiled during image build (no Go runtime required at runtime)
- Ports: API on 3000, Postgres on 5432, Redis on 6379

**Chrome Extension:**
- Manifest V3 — Chrome/Chromium only
- No build step — vanilla ES Modules loaded natively by the browser
- Communicates with local API at `http://localhost:3000` (configurable in extension options)
