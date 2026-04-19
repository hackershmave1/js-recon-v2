# CONVENTIONS
_Last updated: 2026-04-19_

## Summary
The backend is a Python 3.11+ FastAPI application. The frontend consists of a Chrome extension (vanilla ES module JavaScript) and a Jinja2-rendered dashboard. There is no linter or formatter configuration file committed — no `.eslintrc`, `.prettierrc`, `setup.cfg`, `tox.ini`, `.flake8`, or `mypy.ini` found. Conventions are enforced through code review and consistent practice rather than tooling.

---

## Python Conventions

### Naming

**Files:** `snake_case.py` for all Python modules.
- Examples: `comprehensive_extractor.py`, `rep_secrets_extractor.py`, `asset_graph_service.py`

**Classes:** `PascalCase`
- Service classes: `ComprehensiveExtractor`, `EndpointSanitizer`, `SecretRollupService`, `AssetGraphService`
- Pydantic request/response models: `SessionAnalyzeRequest`, `FileIn`, `DependencyIn`, `ReconJobStartRequest`
- SQLAlchemy ORM models: `Session` (aliased as `DbSession`), `File` (aliased as `DbFile`), `FileAnalysis`
- Dataclasses: `ReconRunnerOptions`, `KingfisherRule`, `CleanupCandidate`, `FetchResult`
- Custom exceptions: `SourceMapDecodeError`, `RegexTimeoutError`

**Functions and methods:** `snake_case`
- Examples: `extract_all()`, `sanitize_endpoints()`, `rollup_secrets()`, `get_session_graph()`
- Private helpers prefixed with `_`: `_resolve_extractor_options()`, `_normalize_endpoint_records()`, `_validate_binary()`

**Constants:** `UPPER_SNAKE_CASE` at module level
- Examples: `SESSION_ANALYZE_DEFAULT_OPTIONS`, `NOISY_DOMAINS`, `MALFORMED_WRAPPER_PATTERNS`, `MISS_REASON_TAXONOMY`

**Variables:** `snake_case`
- `analysis_start`, `extractor_options`, `js_url`

**Module-level globals (mutable):** `UPPER_SNAKE_CASE`
- `SESSION_ANALYSIS_JOBS: dict[str, dict[str, Any]]`
- `SESSION_ANALYSIS_LOCK = threading.Lock()`

### Import Style

Imports are organized in this order (no blank line between standard and third-party, blank line before local):
1. Standard library
2. Third-party (`fastapi`, `pydantic`, `sqlalchemy`, `httpx`, etc.)
3. Local (`from ...config import settings`, `from ...models import ...`)

ORM model imports use aliased names to avoid collision with Pydantic/route-level names:
```python
from ...models import Session as DbSession
from ...models import File as DbFile
from ...models import FileAnalysis as DbFileAnalysis
```

Relative imports are used throughout the `app/` package. Avoid absolute imports inside the `app/` package.

### Type Annotations

Mixed style across the codebase:
- **Newer files** (post-Python 3.10 style): use built-in generics and union syntax — `list[str]`, `dict[str, Any]`, `str | None`, `list[dict[str, Any]] | None`
- **Older files**: use `from typing import Any, Dict, List, Optional` with `Optional[str]`, `Dict[str, Any]`, `List[Dict[str, Any]]`
- `from __future__ import annotations` is present in newer service files (`analysis_compactor.py`, `async_utils.py`, `auth_context.py`, `binary_locator.py`, `recon_job_runner.py`) but not universally applied.

**Rule:** New files should use the `from __future__ import annotations` + built-in generics style. Do not mix `Optional[X]` and `X | None` in the same file.

### Docstrings

- Module-level and class-level docstrings use triple-quoted strings: `"""..."""`
- Method docstrings use Google style (Args/Returns/Raises sections) for public methods in security-sensitive classes (`SecurityValidator`, `JSluiceExtractor`)
- Many service methods have no docstring — only complex or public-facing methods are documented
- Do NOT use `#` comments to explain what the next line does; only comment on why

### Logging

**Pattern:** Use `logging.getLogger(__name__)` at module level, stored as `logger`.

```python
logger = logging.getLogger(__name__)
```

**Style is inconsistent** — both f-string and `%`-style are used:
- Newer files (`comprehensive_extractor.py`): `logger.error("REP endpoint extraction failed: %s", exc)` — **preferred**
- Older files (`jsluice_extractor.py`): `logger.error(f"jsluice urls failed: {result.stderr}")` — legacy, avoid in new code

**Use `%`-style lazy interpolation for all new log calls.**

Levels in use:
- `logger.warning(...)` — degraded functionality (binary missing, optional feature unavailable)
- `logger.error(...)` — extractor failures, subprocess errors
- `logger.info(...)` — successful extraction counts

### Error Handling

**Service layer:** Catch specific exceptions, log, and return empty results or re-raise. Do not swallow exceptions silently.

```python
try:
    rep_endpoints = self.rep_endpoints.extract(js_content, js_url)
except Exception as exc:
    logger.error("REP endpoint extraction failed: %s", exc)
```

**Route layer:** Raise `HTTPException` with explicit status codes. Never expose raw exception messages to clients without validation.

```python
raise HTTPException(status_code=404, detail="Session not found")
raise HTTPException(status_code=422, detail="Invalid file content")
raise HTTPException(status_code=500, detail=f"Failed to delete session: {exc}")
```

**Guard pattern for optional integrations** (binaries not available):
```python
try:
    self.jsluice = JSluiceExtractor()
except (FileNotFoundError, PermissionError) as exc:
    logger.warning("jsluice not available: %s", exc)
    self.jsluice = None
```

Bare `except Exception:` (without binding) appears in a few places in routes — this is a known shortcoming, not a pattern to replicate.

### Configuration

All settings live in `api/app/config.py` as a `pydantic_settings.BaseSettings` subclass. Settings are accessed via the singleton `settings` imported from `...config`.

```python
from ...config import settings
settings.storage_path
settings.endpoint_sanitization_enabled
```

Environment variables map directly to field names (no prefix, case-insensitive per `Config.case_sensitive = False`).

### SQLAlchemy / Database

- Session injection via FastAPI `Depends(get_db)` in route handlers
- Tests override `get_db` via `app.dependency_overrides`
- ORM models inherit from `Base` defined in `app/db.py`
- Runtime schema migrations performed in `ensure_runtime_schema_updates()` in `app/main.py` using raw `ALTER TABLE` statements — not through Alembic migrations

### Pydantic Models (Request/Response)

- Request models: named `*Request` (e.g., `SessionAnalyzeRequest`, `FileIn`)
- Response models: named `*Response` (e.g., `SessionAnalysisResponse`)
- Input models that accept extra fields use `ConfigDict(extra="allow")` (e.g., `FileIn`)
- Field names follow camelCase to match the Chrome extension payload convention: `contentHash`, `sessionId`, `capturedAt`, `sourceMapUrl`

### Dataclasses

Used for pure data containers in service layer, not for models that need serialization:
```python
@dataclass
class ReconRunnerOptions:
    urls: list[str]
    session_id: str
    ...
```

`@dataclass(slots=True)` used in `KingfisherRule` for memory efficiency.

---

## JavaScript / Chrome Extension Conventions

### Module System

ES modules (`export class`, `import ... from`) used throughout the `chrome-extension/modules/` directory. Files in the modules directory use named exports.

```javascript
export class BatchUploader { ... }
```

### Naming

- **Files:** `kebab-case.js` (e.g., `batch-uploader.js`, `content-fetcher.js`, `dep-extractor.js`)
- **Classes:** `PascalCase` (e.g., `BatchUploader`, `RepPlusBridge`)
- **Methods/functions:** `camelCase` (e.g., `processBatch()`, `setPerformAnalysisOnUpload()`, `normalizeEndpoint()`)
- **Private-ish fields:** no `#` prefix, but conceptually internal state is not formally private

### Error Handling

- `try/catch` with `console.error(...)` for upload failures
- Errors propagate via `chrome.notifications.create(...)` for user-facing alerts
- Graceful fallback: re-queue failed batches rather than losing data

### Code Style

- No formatter config found; indentation is 2 spaces
- Template literals preferred over string concatenation
- `async/await` throughout (no raw `.then()` chains)

---

## Anti-Patterns to Avoid

1. **Do not add raw SQL `ALTER TABLE` in startup code** — the `ensure_runtime_schema_updates()` function in `main.py` is a technical debt pattern. New schema changes should use Alembic migrations.
2. **Do not use `except Exception:` bare** (without binding and logging) — always bind to a variable and log.
3. **Do not mix `Optional[X]` and `X | None`** in the same file.
4. **Do not use f-strings in log calls** — use `%`-style lazy interpolation.
5. **Do not use `assert` in production code** — assertions are not present in application code and should stay that way.
6. **Do not write bare module-level code with side effects** (outside of `if __name__ == "__main__"`) — all initialization happens in class constructors or route startup events.

---

## Gaps / Unknowns

- No linter (`flake8`, `ruff`, `pylint`) or formatter (`black`, `isort`) configured — code style is maintained manually
- No `mypy` configuration — static type checking is not enforced
- No pre-commit hooks found
- No `.editorconfig` file — editor settings are assumed/implicit
- JavaScript in `chrome-extension/` has no ESLint or Prettier config
- The `api/app/static/dashboard.js` and `dashboard-failure-utils.js` follow no documented convention separate from general JS style
