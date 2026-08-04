# Project-scoped capture sessions — Backend (Plan A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first-class `projects` table (an engagement owning default recon settings), attach sessions to it, and expose project CRUD + config inherit/override plumbing — so the extension (Plan B) and workspace (Plan C) can build on it.

**Architecture:** A pure, schema-driven config module (`project_config.py`) defines the four default groups (scope / capture / denylist / analysis), system defaults, validation, a deep-merge for partial project edits, and a `resolve_effective_config` (null = inherit, set = replace, per field). A new `Project` SQLAlchemy model + a nullable `project_id` (SET NULL on delete) plus `capture_config` and `override_keys` snapshot columns on `sessions`. A new `projects` CRUD router. The `save-files` create seam binds a session's project + snapshot; `PATCH /api/sessions/{id}` gains per-session override editing. Snapshot-on-create: the client resolves the effective config and sends it; the backend stores it as-is and never re-resolves (single-user, not a trust boundary). No server-side scope enforcement.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.0 (legacy `declarative_base`), Alembic (string revision ids), Pydantic v2, pytest (via `uv`), Postgres.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-08-03-project-scoped-capture-sessions-design.md`. Branch: `feat/project-scoped-sessions`.
- **JSON not JSONB** for all list/dict columns (`Column(JSON, ...)`) — repo convention (`session.py` comment: "JSON (not JSONB) for SQLite-test parity").
- **Model imports:** `from ..db import Base`; PK `Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)`; register every model in `api/app/models/__init__.py` (import + `__all__`).
- **Route imports:** `from ...db import get_db`; `db: Session = Depends(get_db)` with `from sqlalchemy.orm import Session`; routers use full inline paths (`@router.get("/api/...")`), registered in `api/app/main.py` (import + `app.include_router(...)`).
- **Alembic:** new revision `"0005"`, `down_revision = "0004"`; `op.add_column(..., server_default=...)`; `downgrade` drops in reverse. Migrations auto-run on API startup (`main.py` → `alembic upgrade head`).
- **Config schema is the single source of truth for the merge** — no per-field branch logic scattered across routes.
- **System defaults (grounded in the extension's current global defaults):** `scope.includeSubdomains=true`, `capture.outOfScopeMode="tag"`, `capture.maxAssetMb=10`, `denylist.useDefaultProfile=true`, `analysis.analyzeOnUpload=false`, `analysis.captureSourceMaps=true`.
- **Bind project/config ONLY on session create** (never on append), mirroring how scope is seeded today in `save_files`.
- **Test harness — two lanes:**
  - *Pure-unit* (no DB): loaded by file path or importing `app` without hitting Postgres. Run: `cd api && uv run pytest tests/<file> -q`.
  - *DB-route* (real Postgres): module-level guard `if not os.getenv("DATABASE_URL"): pytest.skip(..., allow_module_level=True)`, `os.environ.setdefault("STORAGE_PATH", ...)`, `from app.main import app`, `TestClient(app)`, create data via `POST /api/save-files`, isolate with fresh random UUIDs. Run in the api container: `docker compose -f api/docker-compose.yml exec -T api sh -lc "cd /app && uv run pytest tests/<file> -q"`. **Do NOT** use the SQLite `conftest.py` fixtures — they are dead code and can't compile the `postgresql.UUID` PK.

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `api/app/project_config.py` | Create | Pure config schema, system defaults, validate, deep-merge, `resolve_effective_config`, `split_effective` |
| `api/tests/test_project_config.py` | Create | Unit tests for the pure helper (file-path load) |
| `api/app/models/project.py` | Create | `Project` model |
| `api/app/models/session.py` | Modify | Add `project_id` FK (SET NULL), `capture_config`, `override_keys` |
| `api/app/models/__init__.py` | Modify | Register `Project` |
| `api/tests/test_project_model_schema.py` | Create | Metadata-introspection test (no DB) |
| `alembic/versions/0005_projects.py` | Create | Migration: projects table + 3 session columns |
| `api/tests/test_migration_0005_linkage.py` | Create | Revision-chain unit test (file-path load) |
| `api/app/api/routes/projects.py` | Create | Projects CRUD router |
| `api/app/main.py` | Modify | Register projects router |
| `api/tests/test_projects_api.py` | Create | DB-route CRUD tests |
| `api/app/api/routes/ingestion.py` | Modify | Bind `project_id`/`capture_config`/`override_keys` on session create |
| `api/app/api/routes/sessions.py` | Modify | Expose project fields in `list_sessions`; `PATCH` gains `captureConfig`/`overrideKeys` |
| `api/tests/test_ingestion_project_binding.py` | Create | DB-route: create-binding, no-rebind-on-append, delete-nulls |
| `api/tests/test_sessions_override_patch.py` | Create | DB-route: PATCH override editing |

---

## Task 1: Pure config module (`project_config.py`)

**Files:**
- Create: `api/app/project_config.py`
- Test: `api/tests/test_project_config.py`

**Interfaces:**
- Produces:
  - `SYSTEM_DEFAULTS: dict`, `CONFIG_SCHEMA: dict[str, tuple[str, ...]]`
  - `system_defaults() -> dict`
  - `deep_merge(base: dict, patch: dict) -> dict`
  - `validate_config(doc: dict, *, partial: bool = False) -> dict` (raises `ValueError`)
  - `resolve_effective_config(defaults: dict, overrides: dict | None) -> tuple[dict, list[str]]`
  - `split_effective(effective: dict) -> tuple[dict, dict]` → `(scope, capture_config)`

- [ ] **Step 1: Write the failing test**

Create `api/tests/test_project_config.py`:

```python
"""Unit tests for the pure project-config helpers (api/app/project_config.py).

Loaded by file path so the stdlib-only helpers test without the app import chain
(run via pytest, or standalone: python this_file)."""
import importlib.util
import pathlib

_MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "app" / "project_config.py"
_spec = importlib.util.spec_from_file_location("project_config", _MODULE_PATH)
project_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(project_config)

system_defaults = project_config.system_defaults
deep_merge = project_config.deep_merge
validate_config = project_config.validate_config
resolve_effective_config = project_config.resolve_effective_config
split_effective = project_config.split_effective


def test_resolve_inherits_all_when_no_overrides():
    d = system_defaults()
    d["scope"]["rootDomains"] = ["*.acme.com"]
    eff, keys = resolve_effective_config(d, None)
    assert eff["scope"]["rootDomains"] == ["*.acme.com"]
    assert keys == []


def test_resolve_override_replaces_per_field_and_records_key():
    d = system_defaults()
    d["scope"]["rootDomains"] = ["*.acme.com"]
    eff, keys = resolve_effective_config(d, {"scope": {"rootDomains": ["app.acme.com"]}})
    assert eff["scope"]["rootDomains"] == ["app.acme.com"]                 # replaced
    assert eff["scope"]["includeSubdomains"] == d["scope"]["includeSubdomains"]  # inherited
    assert keys == ["scope.rootDomains"]


def test_resolve_list_override_is_replace_not_union():
    d = system_defaults()
    d["denylist"]["rules"] = [{"tag": "a", "pattern": "*.a.com"}]
    eff, keys = resolve_effective_config(d, {"denylist": {"rules": []}})
    assert eff["denylist"]["rules"] == []                                  # replaced, not union
    assert keys == ["denylist.rules"]


def test_validate_rejects_bad_out_of_scope_mode():
    d = system_defaults()
    d["capture"]["outOfScopeMode"] = "nope"
    try:
        validate_config(d)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_validate_rejects_max_asset_mb_over_10():
    d = system_defaults()
    d["capture"]["maxAssetMb"] = 25
    try:
        validate_config(d)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_deep_merge_patch_leaf_wins_and_preserves_siblings():
    base = system_defaults()
    merged = deep_merge(base, {"analysis": {"analyzeOnUpload": True}})
    assert merged["analysis"]["analyzeOnUpload"] is True
    assert merged["analysis"]["captureSourceMaps"] == base["analysis"]["captureSourceMaps"]


def test_split_effective_separates_scope_from_rest():
    scope, cap = split_effective(system_defaults())
    assert set(scope) == {"rootDomains", "includeSubdomains"}
    assert set(cap) == {"capture", "denylist", "analysis"}


def test_validate_partial_only_checks_present_sections():
    validate_config({"analysis": {"analyzeOnUpload": True, "captureSourceMaps": False}}, partial=True)


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"  ok  {_name}")
    print("project_config tests: all assertions passed")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd api && uv run pytest tests/test_project_config.py -q`
Expected: FAIL — `FileNotFoundError` / import error (module `app/project_config.py` does not exist yet).

- [ ] **Step 3: Write the implementation**

Create `api/app/project_config.py`:

```python
"""Project config schema + inherit/override resolution (pure helpers).

A project owns a ``defaults`` document with four groups (scope/capture/denylist/
analysis). A session resolves its effective config once, at creation, from the
project defaults plus a sparse set of per-field overrides: null = inherit, set =
replace (per leaf; list values are replaced, never unioned). These pure helpers
are the single source of truth for the shape and the merge. No DB, stdlib only."""
import copy
from typing import Any

_OUT_OF_SCOPE_MODES = {"tag", "mute", "exclude"}

SYSTEM_DEFAULTS: dict[str, Any] = {
    "scope": {"rootDomains": [], "includeSubdomains": True},
    "capture": {"outOfScopeMode": "tag", "maxAssetMb": 10},
    "denylist": {"rules": [], "useDefaultProfile": True},
    "analysis": {"analyzeOnUpload": False, "captureSourceMaps": True},
}

# Every leaf a project owns and a session may override, grouped by section.
CONFIG_SCHEMA: dict[str, tuple[str, ...]] = {
    "scope": ("rootDomains", "includeSubdomains"),
    "capture": ("outOfScopeMode", "maxAssetMb"),
    "denylist": ("rules", "useDefaultProfile"),
    "analysis": ("analyzeOnUpload", "captureSourceMaps"),
}


def system_defaults() -> dict[str, Any]:
    return copy.deepcopy(SYSTEM_DEFAULTS)


def deep_merge(base: dict, patch: dict) -> dict:
    """Recursively merge ``patch`` into a copy of ``base``; patch leaves win.
    Used to apply a partial project-defaults update over the stored document."""
    out = copy.deepcopy(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def validate_config(doc: dict, *, partial: bool = False) -> dict:
    """Validate a config document against the schema. With ``partial=True`` only
    the sections that are present are checked (used for per-session captureConfig
    and partial project PATCH). Raises ValueError with a human-readable message."""
    if not isinstance(doc, dict):
        raise ValueError("config must be an object")
    for section in CONFIG_SCHEMA:
        if section not in doc:
            if partial:
                continue
            raise ValueError(f"missing config section: {section}")
        if not isinstance(doc[section], dict):
            raise ValueError(f"config section {section} must be an object")

    if "scope" in doc:
        scope = doc["scope"]
        if "rootDomains" in scope and not isinstance(scope["rootDomains"], list):
            raise ValueError("scope.rootDomains must be a list")
        if "includeSubdomains" in scope and not isinstance(scope["includeSubdomains"], bool):
            raise ValueError("scope.includeSubdomains must be a boolean")
    if "capture" in doc:
        capture = doc["capture"]
        if "outOfScopeMode" in capture and capture["outOfScopeMode"] not in _OUT_OF_SCOPE_MODES:
            raise ValueError("capture.outOfScopeMode must be one of tag|mute|exclude")
        if "maxAssetMb" in capture:
            mb = capture["maxAssetMb"]
            if isinstance(mb, bool) or not isinstance(mb, (int, float)) or mb <= 0 or mb > 10:
                raise ValueError("capture.maxAssetMb must be a number in (0, 10]")
    if "denylist" in doc:
        denylist = doc["denylist"]
        if "rules" in denylist:
            if not isinstance(denylist["rules"], list):
                raise ValueError("denylist.rules must be a list")
            for rule in denylist["rules"]:
                if not isinstance(rule, dict) or "pattern" not in rule:
                    raise ValueError("each denylist rule must be an object with a 'pattern'")
        if "useDefaultProfile" in denylist and not isinstance(denylist["useDefaultProfile"], bool):
            raise ValueError("denylist.useDefaultProfile must be a boolean")
    if "analysis" in doc:
        analysis = doc["analysis"]
        for key in ("analyzeOnUpload", "captureSourceMaps"):
            if key in analysis and not isinstance(analysis[key], bool):
                raise ValueError(f"analysis.{key} must be a boolean")
    return doc


def resolve_effective_config(defaults: dict, overrides: dict | None) -> tuple[dict, list[str]]:
    """Resolve a session's effective config from project defaults + sparse overrides.
    Per leaf: use the override if present, else inherit. Returns (effective, override_keys)
    where override_keys is the sorted dotted paths that were overridden."""
    overrides = overrides or {}
    effective = copy.deepcopy(defaults)
    override_keys: list[str] = []
    for section, keys in CONFIG_SCHEMA.items():
        section_override = overrides.get(section)
        if not isinstance(section_override, dict):
            continue
        for key in keys:
            if key in section_override:
                effective.setdefault(section, {})[key] = copy.deepcopy(section_override[key])
                override_keys.append(f"{section}.{key}")
    return effective, sorted(override_keys)


def split_effective(effective: dict) -> tuple[dict, dict]:
    """Split a resolved config into the scope part (stored in session columns) and
    the capture_config part (capture/denylist/analysis, stored as one JSON column)."""
    scope_section = effective.get("scope") or {}
    scope = {
        "rootDomains": list(scope_section.get("rootDomains") or []),
        "includeSubdomains": bool(scope_section.get("includeSubdomains", True)),
    }
    capture_config = {
        section: copy.deepcopy(effective.get(section) or {})
        for section in ("capture", "denylist", "analysis")
    }
    return scope, capture_config
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd api && uv run pytest tests/test_project_config.py -q`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add api/app/project_config.py api/tests/test_project_config.py
git commit -m "feat(projects): pure config schema + inherit/override resolver"
```

---

## Task 2: `Project` model + session columns

**Files:**
- Create: `api/app/models/project.py`
- Modify: `api/app/models/session.py`
- Modify: `api/app/models/__init__.py`
- Test: `api/tests/test_project_model_schema.py`

**Interfaces:**
- Consumes: `Base` from `api/app/db.py`.
- Produces: `Project` model (`projects` table); `Session.project_id` / `Session.capture_config` / `Session.override_keys`.

- [ ] **Step 1: Write the failing test**

Create `api/tests/test_project_model_schema.py`:

```python
"""Model-registration + column-shape checks. Imports the models package (which
attaches every model to Base.metadata) but never opens a DB connection, so it
runs in the pure-unit lane."""
from app.db import Base
from app import models  # noqa: F401  (importing registers models on Base.metadata)


def test_projects_table_registered():
    assert "projects" in Base.metadata.tables


def test_session_has_project_columns():
    cols = Base.metadata.tables["sessions"].columns
    assert "project_id" in cols
    assert "capture_config" in cols
    assert "override_keys" in cols


def test_project_id_is_fk_to_projects_with_set_null():
    fks = list(Base.metadata.tables["sessions"].c.project_id.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "projects"
    assert fks[0].ondelete == "SET NULL"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd api && uv run pytest tests/test_project_model_schema.py -q`
Expected: FAIL — `"projects" not in Base.metadata.tables` (KeyError / assertion).

- [ ] **Step 3a: Create the `Project` model**

Create `api/app/models/project.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, JSON, String
from sqlalchemy.dialects.postgresql import UUID

from ..db import Base


class Project(Base):
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    # The defaults document a session inherits (scope/capture/denylist/analysis).
    # Shape + validation live in app/project_config.py. JSON (not JSONB) per repo convention.
    defaults = Column(JSON, default=dict, nullable=False)
```

- [ ] **Step 3b: Add the session columns**

Modify `api/app/models/session.py`. Add `ForeignKey` to the sqlalchemy import:

```python
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, JSON, String
```

Add these three columns to the `Session` class, immediately after the `include_subdomains` column:

```python
    # Project membership (optional): the engagement this session belongs to. Nullable
    # so sessions can run standalone; SET NULL so deleting a project leaves its
    # sessions loose (their snapshot below is self-contained). See project_config.py.
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    # Snapshot (at create) of the non-scope config groups (capture/denylist/analysis)
    # the session captured under. Scope stays in root_domains/include_subdomains above.
    capture_config = Column(JSON, nullable=True)
    # Dotted config paths the session overrode vs its project (provenance for the UI).
    override_keys = Column(JSON, default=list, nullable=False)
```

- [ ] **Step 3c: Register the model**

Modify `api/app/models/__init__.py`. Add the import (top) and add `"Project"` to `__all__`:

```python
from .project import Project
from .session import Session
from .file import File
from .file_analysis import FileAnalysis
from .dependency import Dependency
from .source_map import SourceMap
from .asset_graph import AssetNode, AssetEdge, DiscoveryMethod, AssetType
from .job import Job
from .finding_status import FindingStatus

__all__ = [
    "Project", "Session", "File", "FileAnalysis", "Dependency", "SourceMap",
    "AssetNode", "AssetEdge", "DiscoveryMethod", "AssetType",
    "Job", "FindingStatus",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd api && uv run pytest tests/test_project_model_schema.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add api/app/models/project.py api/app/models/session.py api/app/models/__init__.py api/tests/test_project_model_schema.py
git commit -m "feat(projects): Project model + session project_id/capture_config/override_keys"
```

---

## Task 3: Alembic migration `0005`

**Files:**
- Create: `alembic/versions/0005_projects.py`
- Test: `api/tests/test_migration_0005_linkage.py`

**Interfaces:**
- Consumes: schema from Task 2 (columns must match the model).
- Produces: applied `projects` table + session columns in the DB (required by Tasks 4–6 DB tests).

- [ ] **Step 1: Write the failing test**

Create `api/tests/test_migration_0005_linkage.py`:

```python
"""Loads the 0005 migration by file path and asserts its revision chain + that it
defines upgrade/downgrade. Pure-unit (executing the module only runs top-level
imports + assignments, not the op.* calls inside upgrade())."""
import importlib.util
import pathlib

_PATH = pathlib.Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0005_projects.py"
_spec = importlib.util.spec_from_file_location("mig0005", _PATH)
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)


def test_revision_chain():
    assert mig.revision == "0005"
    assert mig.down_revision == "0004"


def test_has_upgrade_and_downgrade():
    assert callable(mig.upgrade) and callable(mig.downgrade)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd api && uv run pytest tests/test_migration_0005_linkage.py -q`
Expected: FAIL — `FileNotFoundError` (migration file does not exist).

- [ ] **Step 3: Write the migration**

Create `alembic/versions/0005_projects.py`:

```python
"""projects

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-03

Adds a first-class ``projects`` table (an engagement owning default recon settings)
and attaches sessions to it: ``project_id`` (nullable FK, SET NULL on project
delete), ``capture_config`` (snapshot of the non-scope config groups the session
captured under) and ``override_keys`` (config leaves the session overrode).
Existing sessions are left loose (project_id NULL). See api/app/models/project.py
and api/app/project_config.py.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("defaults", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column("sessions", sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_sessions_project_id", "sessions", "projects",
        ["project_id"], ["id"], ondelete="SET NULL",
    )
    op.add_column("sessions", sa.Column("capture_config", sa.JSON(), nullable=True))
    op.add_column("sessions", sa.Column("override_keys", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("sessions", "override_keys")
    op.drop_column("sessions", "capture_config")
    op.drop_constraint("fk_sessions_project_id", "sessions", type_="foreignkey")
    op.drop_column("sessions", "project_id")
    op.drop_table("projects")
```

- [ ] **Step 4: Run the linkage test**

Run: `cd api && uv run pytest tests/test_migration_0005_linkage.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Apply the migration to the running DB**

The API applies migrations on startup. Restart the api service and confirm the upgrade ran:

Run: `docker compose -f api/docker-compose.yml restart api`
Then: `docker compose -f api/docker-compose.yml logs --tail=60 api`
Expected: log line `Running upgrade 0004 -> 0005, projects` and no traceback; the container reaches "Application startup complete."

Sanity-check the table exists:
Run: `docker compose -f api/docker-compose.yml exec -T api sh -lc "cd /app && uv run python -c \"from app.db import engine; import sqlalchemy as sa; print(sa.inspect(engine).has_table('projects'))\""`
Expected: `True`.

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/0005_projects.py api/tests/test_migration_0005_linkage.py
git commit -m "feat(projects): migration 0005 (projects table + session columns)"
```

---

## Task 4: Projects CRUD router

**Files:**
- Create: `api/app/api/routes/projects.py`
- Modify: `api/app/main.py`
- Test: `api/tests/test_projects_api.py`

**Interfaces:**
- Consumes: `system_defaults`, `deep_merge`, `validate_config` (Task 1); `Project` (Task 2); migration applied (Task 3).
- Produces: `GET/POST /api/projects`, `GET/PATCH/DELETE /api/projects/{id}`; response shape `{ id, name, createdAt, updatedAt, defaults }`.

- [ ] **Step 1: Write the failing test**

Create `api/tests/test_projects_api.py`:

```python
"""DB-route tests for the projects CRUD API. Real Postgres required (run in the
api container). Self-skips if DATABASE_URL is unset."""
import os
import uuid

import pytest
from fastapi.testclient import TestClient

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("DATABASE_URL not set", allow_module_level=True)

os.environ.setdefault("STORAGE_PATH", f"/tmp/js-extractor-test-{uuid.uuid4()}")

from app.main import app

client = TestClient(app)


def test_create_project_fills_system_defaults():
    r = client.post("/api/projects", json={"name": "acme-bounty"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "acme-bounty"
    assert body["defaults"]["scope"]["includeSubdomains"] is True
    assert body["defaults"]["capture"]["maxAssetMb"] == 10
    assert body["defaults"]["capture"]["outOfScopeMode"] == "tag"


def test_create_project_rejects_empty_name():
    r = client.post("/api/projects", json={"name": "   "})
    assert r.status_code == 400


def test_create_project_rejects_bad_config():
    r = client.post("/api/projects", json={"name": "bad", "defaults": {"capture": {"maxAssetMb": 25}}})
    assert r.status_code == 400


def test_get_and_list_project():
    pid = client.post("/api/projects", json={"name": "list-me"}).json()["id"]
    assert client.get(f"/api/projects/{pid}").status_code == 200
    ids = {p["id"] for p in client.get("/api/projects").json()}
    assert pid in ids


def test_get_unknown_project_404():
    assert client.get(f"/api/projects/{uuid.uuid4()}").status_code == 404


def test_patch_deep_merges_defaults():
    pid = client.post("/api/projects", json={"name": "patch-me"}).json()["id"]
    r = client.patch(f"/api/projects/{pid}", json={"defaults": {"analysis": {"analyzeOnUpload": True}}})
    assert r.status_code == 200, r.text
    defaults = r.json()["defaults"]
    assert defaults["analysis"]["analyzeOnUpload"] is True
    assert defaults["analysis"]["captureSourceMaps"] is True  # untouched sibling preserved


def test_delete_project():
    pid = client.post("/api/projects", json={"name": "del-me"}).json()["id"]
    assert client.delete(f"/api/projects/{pid}").status_code == 200
    assert client.get(f"/api/projects/{pid}").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose -f api/docker-compose.yml exec -T api sh -lc "cd /app && uv run pytest tests/test_projects_api.py -q"`
Expected: FAIL — all POST/GET return 404 (route not registered yet).

- [ ] **Step 3a: Write the router**

Create `api/app/api/routes/projects.py`:

```python
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import Project as DbProject
from ...project_config import system_defaults, deep_merge, validate_config

router = APIRouter()


class ProjectCreateRequest(BaseModel):
    name: str
    defaults: dict | None = None


class ProjectUpdateRequest(BaseModel):
    name: str | None = None
    defaults: dict | None = None


def _safe_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project id")


def project_payload(project: DbProject) -> dict:
    return {
        "id": str(project.id),
        "name": project.name,
        "createdAt": project.created_at.isoformat(),
        "updatedAt": project.updated_at.isoformat(),
        "defaults": project.defaults or {},
    }


@router.get("/api/projects")
def list_projects(db: Session = Depends(get_db)):
    rows = db.query(DbProject).order_by(DbProject.created_at.desc()).all()
    return [project_payload(p) for p in rows]


@router.post("/api/projects")
def create_project(request: ProjectCreateRequest, db: Session = Depends(get_db)):
    name = (request.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Project name cannot be empty")
    filled = deep_merge(system_defaults(), request.defaults or {})
    try:
        validate_config(filled)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    project = DbProject(name=name, defaults=filled)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project_payload(project)


@router.get("/api/projects/{project_id}")
def get_project(project_id: str, db: Session = Depends(get_db)):
    project = db.query(DbProject).filter(DbProject.id == _safe_uuid(project_id)).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project_payload(project)


@router.patch("/api/projects/{project_id}")
def update_project(project_id: str, request: ProjectUpdateRequest, db: Session = Depends(get_db)):
    project = db.query(DbProject).filter(DbProject.id == _safe_uuid(project_id)).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if request.name is not None:
        name = request.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Project name cannot be empty")
        project.name = name
    if request.defaults is not None:
        try:
            validate_config(request.defaults, partial=True)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        merged = deep_merge(project.defaults or system_defaults(), request.defaults)
        try:
            validate_config(merged)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        project.defaults = merged
    project.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(project)
    return project_payload(project)


@router.delete("/api/projects/{project_id}")
def delete_project(project_id: str, db: Session = Depends(get_db)):
    project = db.query(DbProject).filter(DbProject.id == _safe_uuid(project_id)).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    db.delete(project)
    db.commit()
    return {"success": True, "id": project_id}
```

- [ ] **Step 3b: Register the router**

Modify `api/app/main.py`. Add the import alongside the other route imports (after the `triage` import):

```python
from .api.routes.triage import router as triage_router
from .api.routes.projects import router as projects_router
```

Add the include call in the `app.include_router(...)` block (after `triage_router`):

```python
app.include_router(triage_router)
app.include_router(projects_router)
```

- [ ] **Step 4: Restart and run tests to verify they pass**

Run: `docker compose -f api/docker-compose.yml restart api`
Then: `docker compose -f api/docker-compose.yml exec -T api sh -lc "cd /app && uv run pytest tests/test_projects_api.py -q"`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add api/app/api/routes/projects.py api/app/main.py api/tests/test_projects_api.py
git commit -m "feat(projects): CRUD router (GET/POST/PATCH/DELETE /api/projects)"
```

---

## Task 5: Bind project + snapshot on session create; expose in session serialization

**Files:**
- Modify: `api/app/api/routes/ingestion.py` (the `save_files` create block + imports + a helper)
- Modify: `api/app/api/routes/sessions.py` (`list_sessions` serialization)
- Test: `api/tests/test_ingestion_project_binding.py`

**Interfaces:**
- Consumes: `validate_config` (Task 1); `Session.project_id/capture_config/override_keys` (Task 2); projects API (Task 4).
- Produces: session rows carry `project_id`/`capture_config`/`override_keys` bound on create; `GET /api/sessions` items include `projectId`, `overrideKeys`, `captureConfig`.

- [ ] **Step 1: Write the failing test**

Create `api/tests/test_ingestion_project_binding.py`:

```python
"""DB-route tests: save-files binds project + config snapshot on create only, and
GET /api/sessions exposes the provenance fields. Real Postgres (run in container)."""
import os
import uuid

import pytest
from fastapi.testclient import TestClient

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("DATABASE_URL not set", allow_module_level=True)

os.environ.setdefault("STORAGE_PATH", f"/tmp/js-extractor-test-{uuid.uuid4()}")

from app.main import app

client = TestClient(app)


def _save(session_id, content_hash, *, project_id=None, capture_config=None, override_keys=None):
    content = f"function b_{content_hash}() {{ return 1; }}"
    meta = {"sessionId": session_id}
    if project_id is not None:
        meta["projectId"] = project_id
    if capture_config is not None:
        meta["captureConfig"] = capture_config
    if override_keys is not None:
        meta["overrideKeys"] = override_keys
    resp = client.post("/api/save-files", json={
        "metadata": meta,
        "files": [{
            "url": f"https://acme.com/{content_hash}.js", "contentHash": content_hash,
            "sessionId": session_id, "contentType": "application/javascript",
            "contentEncoding": "identity", "contentLength": len(content),
            "content": content, "dependencies": [],
        }],
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


def _get_session(session_id):
    rows = client.get("/api/sessions").json()
    match = [s for s in rows if s["id"] == session_id]
    assert match, f"session {session_id} not found"
    return match[0]


def test_save_files_binds_project_and_config_on_create():
    pid = client.post("/api/projects", json={"name": "bind"}).json()["id"]
    sid = str(uuid.uuid4())
    _save(sid, "bind-1", project_id=pid,
          capture_config={"analysis": {"analyzeOnUpload": True, "captureSourceMaps": False}},
          override_keys=["analysis.analyzeOnUpload"])
    s = _get_session(sid)
    assert s["projectId"] == pid
    assert s["overrideKeys"] == ["analysis.analyzeOnUpload"]
    assert s["captureConfig"]["analysis"]["analyzeOnUpload"] is True


def test_save_files_does_not_rebind_on_append():
    pid = client.post("/api/projects", json={"name": "norebind"}).json()["id"]
    sid = str(uuid.uuid4())
    _save(sid, "nr-1")            # create loose (no project)
    _save(sid, "nr-2", project_id=pid)  # append attempts to bind -> must be ignored
    assert _get_session(sid)["projectId"] is None


def test_save_files_rejects_bad_capture_config():
    sid = str(uuid.uuid4())
    resp = client.post("/api/save-files", json={
        "metadata": {"sessionId": sid, "captureConfig": {"capture": {"maxAssetMb": 99}}},
        "files": [{
            "url": "https://acme.com/x.js", "contentHash": "bad-cc", "sessionId": sid,
            "contentType": "application/javascript", "contentEncoding": "identity",
            "contentLength": 10, "content": "var a = 1;", "dependencies": [],
        }],
    })
    assert resp.status_code == 400


def test_delete_project_leaves_session_loose():
    pid = client.post("/api/projects", json={"name": "del-loose"}).json()["id"]
    sid = str(uuid.uuid4())
    _save(sid, "dl-1", project_id=pid)
    assert client.delete(f"/api/projects/{pid}").status_code == 200
    assert _get_session(sid)["projectId"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose -f api/docker-compose.yml exec -T api sh -lc "cd /app && uv run pytest tests/test_ingestion_project_binding.py -q"`
Expected: FAIL — `KeyError: 'projectId'` in `_get_session` (session serialization has no `projectId`), and binding not applied.

- [ ] **Step 3a: Bind on create in `save_files`**

Modify `api/app/api/routes/ingestion.py`. Extend the `session_scope` import line to also import `validate_config` from `project_config`:

```python
from ...session_scope import derive_root_domains, normalize_root_domains
from ...project_config import validate_config
```

Add this helper next to `safe_uuid` (near line 566):

```python
def safe_project_uuid(value):
    """Parse an optional projectId. None/'' -> None; invalid -> HTTP 400."""
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid projectId")
```

Replace the `if not db_session:` create block (the current version seeds only scope) with the version that also binds project + snapshot:

```python
    if not db_session:
        # Seed scope + project membership + config snapshot on create only (later
        # appends never re-bind). The client sends the already-resolved effective
        # config; the backend stores it as-is (snapshot-on-create; single-user).
        meta = payload.metadata or {}
        include_subdomains = meta.get("includeSubdomains")
        explicit_roots = normalize_root_domains(meta.get("rootDomains") or [])
        project_id = safe_project_uuid(meta.get("projectId"))
        capture_config = meta.get("captureConfig")
        if capture_config is not None:
            try:
                validate_config(capture_config, partial=True)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"invalid captureConfig: {exc}")
        override_keys = meta.get("overrideKeys")
        if not isinstance(override_keys, list):
            override_keys = []
        db_session = DbSession(
            id=session_uuid,
            root_domains=explicit_roots or derive_root_domains([f.url for f in payload.files]),
            include_subdomains=True if include_subdomains is None else bool(include_subdomains),
            project_id=project_id,
            capture_config=capture_config,
            override_keys=override_keys,
        )
        db.add(db_session)
        db.commit()
        db.refresh(db_session)
```

- [ ] **Step 3b: Expose project fields in `list_sessions`**

Modify `api/app/api/routes/sessions.py`. In the `list_sessions` return dict (the per-row dict comprehension), add three keys next to `**scope_payload(session)`:

```python
            **scope_payload(session),
            "projectId": str(session.project_id) if session.project_id else None,
            "overrideKeys": list(session.override_keys or []),
            "captureConfig": session.capture_config,
```

- [ ] **Step 4: Restart and run tests to verify they pass**

Run: `docker compose -f api/docker-compose.yml restart api`
Then: `docker compose -f api/docker-compose.yml exec -T api sh -lc "cd /app && uv run pytest tests/test_ingestion_project_binding.py -q"`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add api/app/api/routes/ingestion.py api/app/api/routes/sessions.py api/tests/test_ingestion_project_binding.py
git commit -m "feat(projects): bind project + config snapshot on session create; expose in session list"
```

---

## Task 6: Per-session override editing (`PATCH /api/sessions/{id}`)

**Files:**
- Modify: `api/app/api/routes/sessions.py` (`SessionUpdateRequest` + `update_session`)
- Test: `api/tests/test_sessions_override_patch.py`

**Interfaces:**
- Consumes: `validate_config` (Task 1); session columns (Task 2); `list_sessions` provenance fields (Task 5).
- Produces: `PATCH /api/sessions/{id}` accepts `captureConfig` + `overrideKeys` and persists them; response echoes `projectId`/`overrideKeys`/`captureConfig`.

- [ ] **Step 1: Write the failing test**

Create `api/tests/test_sessions_override_patch.py`:

```python
"""DB-route tests: PATCH edits a session's capture_config override + override_keys.
Real Postgres (run in container)."""
import os
import uuid

import pytest
from fastapi.testclient import TestClient

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("DATABASE_URL not set", allow_module_level=True)

os.environ.setdefault("STORAGE_PATH", f"/tmp/js-extractor-test-{uuid.uuid4()}")

from app.main import app

client = TestClient(app)


def _save(session_id, content_hash):
    content = "function p6(){ return 1; }"
    resp = client.post("/api/save-files", json={
        "metadata": {"sessionId": session_id},
        "files": [{
            "url": f"https://acme.com/{content_hash}.js", "contentHash": content_hash,
            "sessionId": session_id, "contentType": "application/javascript",
            "contentEncoding": "identity", "contentLength": len(content),
            "content": content, "dependencies": [],
        }],
    })
    assert resp.status_code == 200, resp.text


def _get_session(session_id):
    rows = client.get("/api/sessions").json()
    return next(s for s in rows if s["id"] == session_id)


def test_patch_updates_capture_config_and_override_keys():
    sid = str(uuid.uuid4())
    _save(sid, "p6-1")
    r = client.patch(f"/api/sessions/{sid}", json={
        "captureConfig": {"capture": {"outOfScopeMode": "exclude", "maxAssetMb": 5}},
        "overrideKeys": ["capture.outOfScopeMode", "capture.maxAssetMb"],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["captureConfig"]["capture"]["outOfScopeMode"] == "exclude"
    assert set(body["overrideKeys"]) == {"capture.outOfScopeMode", "capture.maxAssetMb"}
    s = _get_session(sid)
    assert s["captureConfig"]["capture"]["maxAssetMb"] == 5


def test_patch_rejects_bad_capture_config():
    sid = str(uuid.uuid4())
    _save(sid, "p6-2")
    r = client.patch(f"/api/sessions/{sid}", json={"captureConfig": {"capture": {"maxAssetMb": 99}}})
    assert r.status_code == 400


def test_patch_scope_still_works():
    sid = str(uuid.uuid4())
    _save(sid, "p6-3")
    r = client.patch(f"/api/sessions/{sid}", json={"rootDomains": ["app.acme.com"], "includeSubdomains": False})
    assert r.status_code == 200, r.text
    assert r.json()["rootDomains"] == ["app.acme.com"]
    assert r.json()["includeSubdomains"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose -f api/docker-compose.yml exec -T api sh -lc "cd /app && uv run pytest tests/test_sessions_override_patch.py -q"`
Expected: FAIL — `test_patch_updates_capture_config_and_override_keys` gets no `captureConfig` in the response (field ignored) / `KeyError`.

- [ ] **Step 3a: Extend the request model**

Modify `api/app/api/routes/sessions.py`. Add the `validate_config` import to the `session_scope` import line:

```python
from ...session_scope import normalize_root_domains, scope_payload
from ...project_config import validate_config
```

Extend `SessionUpdateRequest`:

```python
class SessionUpdateRequest(BaseModel):
    # All fields optional -> partial update. name-only keeps the original rename contract.
    name: str | None = Field(default=None, max_length=120)
    rootDomains: list[str] | None = None
    includeSubdomains: bool | None = None
    captureConfig: dict | None = None
    overrideKeys: list[str] | None = None
```

- [ ] **Step 3b: Handle the new fields in `update_session`**

In `update_session`, after the existing `includeSubdomains` block and before `db.commit()`, add:

```python
    if request.captureConfig is not None:
        try:
            validate_config(request.captureConfig, partial=True)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid captureConfig: {exc}")
        session.capture_config = request.captureConfig
    if request.overrideKeys is not None:
        session.override_keys = list(request.overrideKeys)
```

Replace the PATCH `return {...}` block so the response echoes the project/provenance fields:

```python
    return {
        "success": True,
        "id": str(session.id),
        "name": session.name,
        "createdAt": session.created_at.isoformat(),
        "source": session.source,
        "version": session.version,
        **scope_payload(session),
        "projectId": str(session.project_id) if session.project_id else None,
        "overrideKeys": list(session.override_keys or []),
        "captureConfig": session.capture_config,
    }
```

- [ ] **Step 4: Restart and run tests to verify they pass**

Run: `docker compose -f api/docker-compose.yml restart api`
Then: `docker compose -f api/docker-compose.yml exec -T api sh -lc "cd /app && uv run pytest tests/test_sessions_override_patch.py -q"`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the full new-test set + the scope regression**

Run: `docker compose -f api/docker-compose.yml exec -T api sh -lc "cd /app && uv run pytest tests/test_project_config.py tests/test_project_model_schema.py tests/test_migration_0005_linkage.py tests/test_projects_api.py tests/test_ingestion_project_binding.py tests/test_sessions_override_patch.py tests/test_session_scope.py -q"`
Expected: all PASS (existing `test_session_scope.py` still green — no scope regression).

- [ ] **Step 6: Commit**

```bash
git add api/app/api/routes/sessions.py api/tests/test_sessions_override_patch.py
git commit -m "feat(projects): per-session config override via PATCH /api/sessions/{id}"
```

---

## Self-Review

**1. Spec coverage:**
- §5 config schema/defaults → Task 1. §6 projects table + session columns + migration (nullable, SET NULL) → Tasks 2–3. §7 projects CRUD + save-files seam ("bind on create", client-resolved stored as-is) + PATCH captureConfig → Tasks 4–6. §11 no server-side enforcement → honored (nothing classifies scope; config is stored/validated only). §12 migration leaves existing sessions loose (`project_id` nullable, no backfill) → Task 3. §13 testing (resolve unit tests incl. list-replace-not-union, CRUD, deep-merge PATCH, DELETE-nulls, save-files bind, PATCH override) → Tasks 1,4,5,6.
- Not in Plan A (correctly deferred to B/C or out of scope): extension flow (Plan B), workspace UI (Plan C), crawler project picker (§10, thin — folded into Plan C/B where NewReconModal lives), "apply to existing" + reassign (§14 fast-follows).

**2. Placeholder scan:** none — every step has runnable code/commands and exact expected output.

**3. Type consistency:** `resolve_effective_config` returns `(effective, override_keys)` (Task 1) and is not re-invoked with a different shape elsewhere. `validate_config(doc, *, partial=False)` used consistently (partial for captureConfig in Tasks 5–6 and the project PATCH patch-doc in Task 4; full for filled project defaults in Task 4). `project_payload`/response keys `projectId`/`overrideKeys`/`captureConfig` are spelled identically in ingestion serialization (Task 5), `list_sessions` (Task 5) and `update_session` (Task 6). Migration column names (`project_id`/`capture_config`/`override_keys`) match the model (Task 2) and the FK constraint name `fk_sessions_project_id` is used in both `create_foreign_key` and `drop_constraint`.
