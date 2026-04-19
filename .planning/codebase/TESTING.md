# TESTING
_Last updated: 2026-04-19_

## Summary
Testing uses pytest for Python backend tests and Node.js native test runner (no framework) for Chrome extension JavaScript tests. The Python test suite is large and well-organized, with 40+ test files split across unit tests for services and integration tests against a live database. JavaScript tests run as standalone `.mjs` scripts using Node.js `assert/strict`. There is no CI pipeline configured (no `.github/workflows/` found).

---

## Python Test Framework

**Runner:** pytest 7.4.3 (pinned in `api/pyproject.toml`)
**Config file:** None found (no `pytest.ini`, no `[tool.pytest]` section in `pyproject.toml`)
**Run from:** `api/` directory

**Run commands:**
```bash
cd api
python -m pytest                          # Run all tests
python -m pytest tests/test_b021_*.py    # Run a single file
python -m pytest -m security             # Run by marker
python -m pytest -m integration         # Run integration tests only
python -m pytest -m "not slow"          # Skip slow tests
python -m pytest --cov=app              # With coverage (pytest-cov installed)
```

**Dependencies declared in `api/pyproject.toml`:**
- `pytest==7.4.3`
- `pytest-cov>=7.0.0`
- `pytest-asyncio>=0.23.8`

---

## Test File Organization

**Primary location:** `api/tests/` — all Python backend tests

**Secondary location:** `tests/` (project root) — 4 older integration tests that import via `api.app.*` path and run from the project root

**JavaScript tests:** `api/tests/` (mixed in with Python) and `chrome-extension/tests/`

**Naming conventions:**
- Feature/bug ticket tests: `test_b{NNN}_{description}.py` — "B" for backlog/bug items
  - Examples: `test_b021_endpoint_sanitizer.py`, `test_b027_asset_graph.py`
- Task-based tests: `test_t{NNN}_{description}.py` — "T" for task items
  - Examples: `test_t029_api_contract_hardening.py`, `test_t036_global_stats.py`
- Legacy/thematic tests: `test_{topic}.py`
  - Examples: `test_security_utils.py`, `test_ingestion.py`, `test_rep_extractors.py`
- JavaScript tests: `test_t{NNN}_{description}.mjs`
  - Examples: `test_t007_batch_uploader_payload.mjs`, `test_t023_dashboard_failure_utils.mjs`

**No co-location:** Tests are centralized in `api/tests/`, not co-located with source files.

---

## Test Structure (Python)

### Class-based organization

Most test files use a class-per-concept structure with `setup_method` for per-test initialization:

```python
class TestEndpointSanitizer:
    """Test the EndpointSanitizer service directly."""

    def setup_method(self):
        """Setup fresh sanitizer for each test."""
        self.sanitizer = EndpointSanitizer()

    def test_malformed_wrapper_removal(self):
        """Test removal of unbalanced brackets and quotes."""
        ...
```

### DB-dependent integration tests

Integration tests that require a real PostgreSQL database guard with a module-level skip:

```python
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("DATABASE_URL not set", allow_module_level=True)
```

These tests create a `TestClient(app)` directly and interact with a real DB session, using explicit teardown to delete test data in a `finally` block.

### Fixture-based unit tests

Unit tests that don't need a database inject mocks via `setup_method` or use `unittest.mock.Mock`:

```python
def setup_method(self):
    self.mock_db = Mock()
    self.service = AssetGraphService(self.mock_db)
    self.session_id = str(uuid.uuid4())
```

---

## Fixtures (`api/tests/conftest.py`)

All shared fixtures are in `api/tests/conftest.py`. Key fixtures:

| Fixture | Scope | Purpose |
|---|---|---|
| `test_db_engine` | session | In-memory SQLite engine with schema created |
| `test_db_session` | function | SQLAlchemy session, rolled back after each test |
| `test_client` | function | FastAPI `TestClient` with DB override via `dependency_overrides` |
| `temp_storage_dir` | function | Temp dir, sets `STORAGE_PATH` env var, cleaned up after |
| `sample_js_files` | function | Dict of JS strings (basic, with_secrets, with_endpoints, minified, etc.) |
| `sample_source_map` | function | A v3 source map dict for testing |
| `mock_jsluice_binary` | function | Shell script mock for jsluice |
| `mock_sourcemapper_binary` | function | Shell script mock for sourcemapper |
| `malicious_payloads` | function | Dict of injection payloads (command injection, path traversal, XSS, SQLi, XXE) |
| `large_js_content` | function | 100-function JS string for performance testing |

DB override pattern used in `test_client`:
```python
def override_get_db():
    try:
        yield test_db_session
    finally:
        pass

app.dependency_overrides[get_db] = override_get_db
```

---

## Custom Pytest Markers

Registered in `conftest.py`:
- `@pytest.mark.security` — auto-applied to tests with "security" or "malicious" in the name
- `@pytest.mark.integration` — auto-applied to tests with "api" in path or "endpoint" in name
- `@pytest.mark.slow` — auto-applied to tests with "large" or "performance" in name

---

## Mocking Patterns

### `unittest.mock`

```python
from unittest.mock import AsyncMock, MagicMock, patch, call

# Mock a database session
self.mock_db = Mock()
self.mock_db.add = Mock()
self.mock_db.flush = Mock()

# Patch subprocess for async tests
with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock,
           side_effect=lambda *a, **k: next(procs)):
    await runner._run_vespasian_scan("https://example.com")
```

### `monkeypatch`

Used in integration tests to override settings:
```python
def test_global_stats_dedupes_endpoints_and_secrets(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
```

### Binary mocks

`conftest.py` provides shell script mocks for external binaries (jsluice, sourcemapper) via `tempfile.NamedTemporaryFile` with `chmod 755`. These are used when testing extraction logic without installing the actual binary.

---

## Async Testing

`pytest-asyncio` is installed. Async tests use `@pytest.mark.asyncio` at class or method level:

```python
@pytest.mark.asyncio
class TestRunVespasianScan:
    async def test_success_copies_spec_to_session_storage(self, tmp_path: Path):
        ...
```

Individual async methods within non-async classes:
```python
@pytest.mark.asyncio
async def test_fetch_with_retry(self):
    ...
```

---

## Integration Test Pattern (DB-backed)

Tests that talk to a real database follow this teardown pattern to avoid test pollution:

```python
db = SessionLocal()
created_session_ids = []
try:
    # ... create test data ...
    db.commit()

    # ... assertions ...
finally:
    if created_session_ids:
        db.query(DbFileAnalysis).filter(...).delete(synchronize_session=False)
        db.query(DbFile).filter(...).delete(synchronize_session=False)
        db.query(DbSession).filter(...).delete(synchronize_session=False)
        db.commit()
    db.close()
```

---

## API Contract Testing Pattern

`test_t029_api_contract_hardening.py` tests that the API rejects invalid inputs with the correct HTTP status codes:

```python
def test_save_files_rejects_invalid_file_url(self):
    payload = self.build_base_payload()
    payload["files"][0]["url"] = "ftp://example.com/app.js"
    response = self.client.post("/api/save-files", json=payload)
    assert response.status_code == 422
    assert "Invalid file url" in response.json()["detail"]
```

---

## JavaScript Test Pattern

Chrome extension tests run as standalone Node.js ESM scripts using `node:assert/strict`. No test runner — they exit non-zero on failure and print `ok` on success.

```bash
node api/tests/test_t023_dashboard_failure_utils.mjs
node chrome-extension/tests/test_t007_batch_uploader_payload.mjs
```

**Technique:** The source module is loaded via `fs.readFileSync` + `vm.runInContext` to avoid needing a DOM or Chrome runtime:
```javascript
const source = fs.readFileSync(uploaderPath, 'utf8');
const transformed = source.replace('export class BatchUploader', 'class BatchUploader');
vm.createContext(sandbox);
vm.runInContext(`${transformed}\nthis.BatchUploader = BatchUploader;`, sandbox, { filename: uploaderPath });
```

---

## Test Coverage

**Coverage tooling:** `pytest-cov` installed, but no coverage thresholds configured and no CI to enforce them.

**Well-tested areas:**
- `api/app/services/endpoint_sanitizer.py` — comprehensive unit tests in `test_b021_endpoint_sanitizer.py`
- `api/app/services/security_utils.py` — comprehensive unit tests in `test_security_utils.py`
- `api/app/services/asset_graph_service.py` — comprehensive unit tests in `test_b027_asset_graph.py`
- `api/app/services/secret_rollup.py` — comprehensive unit tests in `test_b025_secret_rollup.py`
- `api/app/services/recon_job_runner.py` (Vespasian paths) — async unit tests in `test_t030_vespasian_runner.py`
- API contract validation — `test_t029_api_contract_hardening.py`

**Partially tested / integration-only:**
- `api/app/api/routes/sessions.py` — large file with some integration coverage but no dedicated unit tests for helper functions like `normalize_session_analysis_options`
- `api/app/main.py` (`ensure_runtime_schema_updates`) — not directly tested
- `api/app/services/comprehensive_extractor.py` — covered indirectly through integration tests

**Not tested:**
- `api/app/services/analysis_compactor.py` — no test file found
- `api/app/services/binary_locator.py` — no test file found
- `api/app/services/file_priority.py` — `test_b030_file_priority.py` exists but is new/pending
- Dashboard JavaScript (`api/app/static/dashboard.js`) — no test file found (dashboard-failure-utils has one test)
- Chrome extension `popup.js`, `options.js`, `background.js` — no tests

---

## CI / CD

**No CI pipeline configured.** No `.github/workflows/` directory found. Tests are run manually.

---

## Gaps / Unknowns

- No `pytest.ini` or `[tool.pytest.ini_options]` section — pytest configuration (asyncio_mode, test paths, markers) relies on defaults and conftest.py hooks only
- `asyncio_mode` not set — if `pytest-asyncio >= 0.21`, the default mode may require explicit `asyncio_mode = "auto"` in config
- Coverage requirements are undefined — no minimum threshold enforced
- JavaScript tests have no test runner (no `package.json` scripts) — must be invoked manually per file
- The `tests/` (root-level) directory uses `api.app.*` import paths that require the project root to be on `PYTHONPATH` — this may not work out of the box
- No snapshot testing, no property-based testing (e.g., Hypothesis)
