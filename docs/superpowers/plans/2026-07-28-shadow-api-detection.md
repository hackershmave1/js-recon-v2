# Shadow-API detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover full client API paths (resolve base URLs in the extractor) and diff them against a user-supplied OpenAPI/Swagger spec, classifying each endpoint finding documented / shadow / unresolved — never flagging a partial path as shadow.

**Architecture:** Phase 1 adds a conservative, scope-safe base-URL pre-pass to the tree-sitter extractor so recovered paths are full. Phase 2 adds a new `recon.spec` package (ingest → classify) plus two RLS tables that mirror `finding_triage`; an analyst attaches a spec via `POST /runs/{run_id}/spec`, classification is stored session-scoped and surfaced per-run, and it auto-re-runs at analyze-finalize.

**Tech Stack:** Python 3.12, tree-sitter / tree-sitter-javascript, SQLAlchemy + Alembic + Postgres (RLS), FastAPI, `openapi-spec-validator` (new dep), PyYAML (hardened), pytest; React + Vite + Vitest (UI).

## Global Constraints

- **Design spec:** `docs/superpowers/specs/2026-07-28-shadow-api-detection-design.md` — every task implements a part of it; §ref given per task. Gate-passed (§13).
- **Honesty (never a false shadow):** a wrong base is worse than none; anything ambiguous / partial / interpolated / base-less → `unresolved`, never `shadow`. Never guess a base.
- **Tests are host-lane:** run with `./.venv/Scripts/python.exe -m pytest <path> -v`; no task requires katana/engines/network. Tests are colocated (`foo_test.py` next to `foo.py`).
- **RLS on every new tenant-scoped table:** carry `tenant_id` NOT NULL; add to a `*_TABLES` tuple; the migration ENABLEs + FORCEs RLS + creates the `tenant_isolation` policy + GRANTs to `recon_app` (mirror `0004_finding_triage.py`).
- **Identity:** the base-URL fold changes `finding_hash` for rebased endpoints (accepted, §3.4); the classifier's wildcard compare-key is coarser than identity templating and lives only in `recon.spec` — it must never touch `finding_hash`.
- **Conventional Commits, one per task**, ending with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Keep committed placeholder secrets format-broken** (GitHub push protection).

## File Structure

- `src/recon/findings/extract.py` (modify) — base-env pre-pass + join at sink. `extract_test.py` (modify).
- `src/recon/spec/__init__.py` (create).
- `src/recon/spec/ingest.py` (create) + `ingest_test.py` — parse/validate/harden a spec → documented op set.
- `src/recon/spec/classify.py` (create) + `classify_test.py` — compare-key, partial detection, decision order, summary.
- `src/recon/spec/service.py` (create) + `service_test.py` — attach/store/classify/persist + reclassify.
- `src/recon/db/models.py` (modify) — `SessionSpec`, `FindingSpecStatus`, `SPEC_TABLES`.
- `src/recon/migrations/versions/0006_spec_diff.py` (create) — the two tables + RLS.
- `src/recon/storage.py` (modify) — add `"spec"` to `BLOB_KINDS`.
- `src/recon/findings/queries.py` (modify) — `spec_status` per finding + run-scoped `spec` summary. `queries_test.py` (modify).
- `src/recon/api/findings_router.py` (modify) — surface `spec_status` + `spec` block.
- `src/recon/api/spec_router.py` (create) + `spec_router_test.py` — `POST /runs/{run_id}/spec`.
- `src/recon/api/app.py` (modify) — mount `spec_router`.
- `src/recon/runs/coordinator.py` (modify) — auto-reclassify hook in `advance`.
- `web/src/...` (modify) — spec upload, status chip, shadow filter + Vitest.

---

## Task 1: Base-environment collection (scope-safe)

Implements spec §3.1, §3.3 (gate B1). A pure pre-pass over the AST that records only statically-certain, unshadowed literal bases.

**Files:**
- Modify: `src/recon/findings/extract.py`
- Test: `src/recon/findings/extract_test.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class BaseEnv:
      instances: dict[str, str | None]   # axios.create var name -> base literal, or None if base not statically known
      default_base: str | None           # axios.defaults.baseURL literal
      const_prefixes: dict[str, str]      # const name -> string literal (for `${NAME}` template prefixes)

  def collect_base_env(root: Node, data: bytes) -> BaseEnv: ...
  ```
- A name that is re-introduced anywhere (nested `const/let/var`, function declaration, or formal/catch/arrow parameter) is **excluded** from `instances` and `const_prefixes` (scope-poisoned).

- [ ] **Step 1: Write the failing test**

```python
# extract_test.py
from recon.findings.extract import collect_base_env, _PARSER

def _env(src: str):
    return collect_base_env(_PARSER.parse(src.encode()).root_node, src.encode())

def test_collect_base_env_axios_create_literal():
    env = _env("const loc = axios.create({ baseURL: '/location' });")
    assert env.instances == {"loc": "/location"}

def test_collect_base_env_defaults_and_const_prefix():
    env = _env("axios.defaults.baseURL = 'https://h/api'; const API = '/v3';")
    assert env.default_base == "https://h/api"
    assert env.const_prefixes["API"] == "/v3"

def test_collect_base_env_unknown_base_is_none_not_dropped():
    env = _env("const c = window.cfg; const loc = axios.create({ baseURL: c });")
    assert env.instances["loc"] is None  # recognized instance, base unknown

def test_collect_base_env_scope_collision_poisons_name():
    env = _env("const loc = axios.create({ baseURL: '/a' }); items.forEach((loc) => loc.get('/x'));")
    assert "loc" not in env.instances  # param `loc` shadows -> unresolvable

def test_collect_base_env_reassignment_poisons_name():
    env = _env("let loc = axios.create({ baseURL: '/a' }); loc = other;")
    assert "loc" not in env.instances
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/findings/extract_test.py -k collect_base_env -v`
Expected: FAIL (`cannot import name 'collect_base_env'`).

- [ ] **Step 3: Implement `collect_base_env`**

```python
# extract.py — add near the tree helpers
from dataclasses import dataclass, field

@dataclass(frozen=True)
class BaseEnv:
    instances: dict[str, str | None]
    default_base: str | None
    const_prefixes: dict[str, str]

def _declared_names(root: Node) -> set[str]:
    """Every identifier introduced as a binding — used to detect shadowing.
    A name bound more than once (any kind) is ambiguous and must not resolve."""
    seen: dict[str, int] = {}
    for node in _walk(root):
        names: list[str] = []
        if node.type in ("variable_declarator", "required_parameter", "optional_parameter"):
            name = node.child_by_field_name("name")
            if name is not None and name.type == "identifier":
                names.append(_text(name))
        elif node.type == "identifier" and node.parent is not None and (
            node.parent.type in ("formal_parameters", "arrow_function", "catch_clause")
            or (node.parent.type == "function_declaration"
                and node.parent.child_by_field_name("name") is node)
        ):
            names.append(_text(node))
        for n in names:
            seen[n] = seen.get(n, 0) + 1
    return {n for n, c in seen.items() if c > 1}

def collect_base_env(root: Node, data: bytes) -> BaseEnv:
    poisoned = _declared_names(root)
    instances: dict[str, str | None] = {}
    default_base: str | None = None
    const_prefixes: dict[str, str] = {}
    for node in _walk(root):
        if node.type == "variable_declarator":
            name_node = node.child_by_field_name("name")
            value = node.child_by_field_name("value")
            if name_node is None or name_node.type != "identifier" or value is None:
                continue
            name = _text(name_node)
            if name in poisoned:
                continue
            if _is_axios_create(value):
                instances[name] = _base_url_arg(value)
            else:
                lit = _string_value(value)
                if lit is not None:
                    const_prefixes[name] = lit
        elif node.type == "assignment_expression":
            left = _text(node.child_by_field_name("left"))
            if left in ("axios.defaults.baseURL",):
                default_base = _string_value(node.child_by_field_name("right"))
    return BaseEnv(instances=instances, default_base=default_base, const_prefixes=const_prefixes)

def _is_axios_create(node: Node) -> bool:
    if node.type != "call_expression":
        return False
    fn = node.child_by_field_name("function")
    return fn is not None and fn.type == "member_expression" \
        and _text(fn.child_by_field_name("object")) == "axios" \
        and _text(fn.child_by_field_name("property")) == "create"

def _base_url_arg(create_call: Node) -> str | None:
    args = _args(create_call)
    if args and args[0].type == "object":
        return _string_value(_object_pairs(args[0]).get("baseURL"))
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/findings/extract_test.py -k collect_base_env -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/recon/findings/extract.py src/recon/findings/extract_test.py
git commit -m "feat(extract): collect scope-safe axios base-URL bindings" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Join base URLs at the sink

Implements spec §3.2, §3.4. Wire `BaseEnv` into extraction so instance calls, bare-axios+defaults, and `${CONST}` template prefixes produce full paths; unknown-base instances are attributed (relative) not dropped; `.open` still routes to XHR.

**Files:**
- Modify: `src/recon/findings/extract.py:64-74` (`extract`), `:191-214` (`_handle_call`), `:217-226` (`_dispatch_member`)
- Test: `src/recon/findings/extract_test.py`

**Interfaces:**
- Consumes: `BaseEnv` from Task 1.
- Produces: `extract(source)` now threads a module-level `BaseEnv` through the call handlers; `_join_base(base: str, path: str) -> str`.

- [ ] **Step 1: Write the failing test**

```python
def _urls(src: str):
    return [(e.method, e.url) for e in extract(src).endpoints]

def test_axios_create_instance_call_joins_base():
    assert ("POST", "/location/address/search") in _urls(
        "const loc = axios.create({ baseURL: '/location' }); loc.post('/address/search', b);")

def test_axios_defaults_base_joins_bare_call():
    assert ("GET", "https://h/api/pets") in _urls(
        "axios.defaults.baseURL = 'https://h/api'; axios.get('/pets');")

def test_const_prefix_template_folds():
    assert ("GET", "/v3/pets") in _urls("const API = '/v3'; fetch(`${API}/pets`);")

def test_unknown_base_instance_attributed_relative_not_dropped():
    # recognized instance, base unknown -> endpoint present with the relative path
    assert ("GET", "/x") in _urls("const c = w.c; const a = axios.create({ baseURL: c }); a.get('/x');")

def test_absolute_url_ignores_base():
    assert ("GET", "https://other/z") in _urls(
        "const loc = axios.create({ baseURL: '/location' }); loc.get('https://other/z');")

def test_open_on_instance_still_routes_to_xhr():
    # `.open(METHOD, url)` on any receiver keeps the XHR shape, not axios-join
    assert ("GET", "/raw") in _urls(
        "const loc = axios.create({ baseURL: '/location' }); loc.open('GET', '/raw');")
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/findings/extract_test.py -k "join or prefix or instance or open_on_instance or absolute_url" -v`
Expected: FAIL (instance calls currently dropped; no join).

- [ ] **Step 3: Implement the join**

```python
# extract.py — join helper
def _join_base(base: str, path: str) -> str:
    if not base:
        return path
    if "://" in path or path.startswith("//"):
        return path  # absolute path wins
    return base.rstrip("/") + "/" + path.lstrip("/")

# extract(): build env once, thread it
def extract(source: str | bytes) -> Extraction:
    data = source.encode("utf-8") if isinstance(source, str) else source
    tree = _PARSER.parse(data)
    env = collect_base_env(tree.root_node, data)
    result = Extraction()
    for node in _walk(tree.root_node):
        if node.type == "call_expression":
            _handle_call(node, result, env)
        elif node.type == "new_expression":
            _handle_new(node, result)
    return result
```
Thread `env` into `_handle_call` / `_dispatch_member` (add the param). In `_dispatch_member`, AFTER the existing `prop == "open"` and `obj in _GLOBAL_OBJECTS`/`axios`/`_JQUERY` branches, add instance dispatch:
```python
    elif obj in env.instances:
        base = env.instances[obj]              # may be None (unknown base)
        _axios_member(call, prop, result, base=base or "")
```
`_axios_member` / `_axios_call` / `_fetch` gain an optional `base: str = ""`; wherever they resolve `url = _string_value(...)`, wrap with `url = _join_base(base, url)` (fetch/defaults) and, for the const-prefix case in `_string_value`-of-template, fold a leading `${NAME}` using `env.const_prefixes`. For bare `axios`/`axios.<verb>` pass `base=env.default_base or ""`. (Const-prefix folding: in `_fetch`, when arg0 is a `template_string` whose first interpolation is `${NAME}` with `NAME in env.const_prefixes`, replace that prefix before templating.)

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/findings/extract_test.py -v`
Expected: PASS (all extract tests, incl. Task 1).

- [ ] **Step 5: Commit**

```bash
git add src/recon/findings/extract.py src/recon/findings/extract_test.py
git commit -m "feat(extract): resolve base URLs at the call site" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Data model + migration 0006

Implements spec §6.1, §6.2 (gate B6). Two RLS tables + the `"spec"` blob kind.

**Files:**
- Modify: `src/recon/db/models.py` (after `FindingTriage`), `src/recon/storage.py:24`
- Create: `src/recon/migrations/versions/0006_spec_diff.py`
- Test: `src/recon/db/spec_model_test.py` (create)

**Interfaces:**
- Produces: `SessionSpec`, `FindingSpecStatus` models; `SPEC_TABLES = ("session_spec", "finding_spec_status")`; `"spec"` in `BLOB_KINDS`.

- [ ] **Step 1: Write the failing test**

```python
# spec_model_test.py
from recon.db import models

def test_spec_tables_registered_and_tenant_scoped():
    assert models.SPEC_TABLES == ("session_spec", "finding_spec_status")
    assert "tenant_id" in models.FindingSpecStatus.__table__.columns
    assert "tenant_id" in models.SessionSpec.__table__.columns

def test_finding_spec_status_unique_on_session_hash():
    uqs = {tuple(c.name for c in u.columns) for u in models.FindingSpecStatus.__table__.constraints
           if u.__class__.__name__ == "UniqueConstraint"}
    assert ("session_id", "finding_hash") in uqs

def test_spec_blob_kind_registered():
    from recon import storage
    assert "spec" in storage.BLOB_KINDS
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/db/spec_model_test.py -v`
Expected: FAIL (`SPEC_TABLES`/`SessionSpec` undefined).

- [ ] **Step 3: Implement models + blob kind**

```python
# models.py — after FindingTriage
class SessionSpec(Base):
    __tablename__ = "session_spec"
    __table_args__ = (
        UniqueConstraint("session_id", name="uq_session_spec_session"),
        CheckConstraint("spec_format IN ('openapi-3', 'swagger-2')", name="ck_session_spec_format"),
        Index("ix_session_spec_tenant", "tenant_id", "session_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("session.id", ondelete="CASCADE"), nullable=False)
    spec_ref: Mapped[str] = mapped_column(Text, nullable=False)
    spec_format: Mapped[str] = mapped_column(String(16), nullable=False)
    server_bases: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    operation_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    actor: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _now_col(nullable=False)
    updated_at: Mapped[dt.datetime] = _now_col(nullable=False)

class FindingSpecStatus(Base):
    __tablename__ = "finding_spec_status"
    __table_args__ = (
        UniqueConstraint("session_id", "finding_hash", name="uq_spec_status_session_finding"),
        CheckConstraint("status IN ('documented', 'shadow', 'unresolved')", name="ck_spec_status"),
        Index("ix_spec_status_session", "tenant_id", "session_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("session.id", ondelete="CASCADE"), nullable=False)
    finding_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(32))
    matched_operation: Mapped[str | None] = mapped_column(Text)
    spec_ref: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = _now_col(nullable=False)
    updated_at: Mapped[dt.datetime] = _now_col(nullable=False)

# after ASSET_TABLES:
SPEC_TABLES: tuple[str, ...] = ("session_spec", "finding_spec_status")
```
```python
# storage.py:24
BLOB_KINDS = frozenset({"input", "raw_js", "source_map", "reconstructed", "report", "assets", "spec"})
```

- [ ] **Step 4: Write migration 0006 (mirror 0004)**

```python
# 0006_spec_diff.py
from __future__ import annotations
from alembic import op
from recon.db import models
from recon.db.base import Base

revision = "0006_spec_diff"
down_revision = "0005_run_asset"  # verified against 0005_run_asset.py
branch_labels = None
depends_on = None
APP_ROLE = "recon_app"

def upgrade() -> None:
    Base.metadata.create_all(op.get_bind())  # idempotent: only the two new tables
    for table in models.SPEC_TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"')
        op.execute(
            f'CREATE POLICY tenant_isolation ON "{table}" '
            "USING (tenant_id::text = current_setting('app.current_tenant', true)) "
            "WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true))"
        )
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table}" TO {APP_ROLE}')

def downgrade() -> None:
    for table in models.SPEC_TABLES:
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"')
    op.drop_table("finding_spec_status")
    op.drop_table("session_spec")
```
(`down_revision` is verified against `0005_run_asset.py`.)

- [ ] **Step 5: Run + commit**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/db/spec_model_test.py -v` → PASS.
```bash
git add src/recon/db/models.py src/recon/storage.py src/recon/migrations/versions/0006_spec_diff.py src/recon/db/spec_model_test.py
git commit -m "feat(db): add session_spec + finding_spec_status tables with RLS" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Spec ingest (hardened)

Implements spec §4, §4.1 (gates B4, B5).

**Files:**
- Create: `src/recon/spec/__init__.py`, `src/recon/spec/ingest.py`, `src/recon/spec/ingest_test.py`
- Modify: `pyproject.toml` / requirements — add `openapi-spec-validator`.

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class DocumentedOp: method: str; path: str          # path = server-base-prefixed, raw (pre compare-key)
  @dataclass(frozen=True)
  class IngestedSpec: format: str; server_bases: list[str]; documented: tuple[DocumentedOp, ...]
  class SpecError(ValueError): ...                     # -> HTTP 422
  def ingest_spec(raw: bytes) -> IngestedSpec: ...
  ```

- [ ] **Step 1: Write the failing test**

```python
# ingest_test.py
import pytest
from recon.spec.ingest import ingest_spec, SpecError

OPENAPI3 = b"""openapi: 3.0.0
info: {title: t, version: '1'}
servers: [{url: '/api/{v}', variables: {v: {default: v2}}}]
paths: {/pets: {get: {responses: {'200': {description: ok}}}}}
"""

def test_openapi3_resolves_server_variable():
    spec = ingest_spec(OPENAPI3)
    assert spec.format == "openapi-3"
    assert ("GET", "/api/v2/pets") in [(o.method, o.path) for o in spec.documented]

def test_swagger2_basepath():
    raw = b'{"swagger":"2.0","basePath":"/v1","paths":{"/x":{"post":{"responses":{}}}}}'
    spec = ingest_spec(raw)
    assert spec.format == "swagger-2"
    assert ("POST", "/v1/x") in [(o.method, o.path) for o in spec.documented]

def test_invalid_spec_raises():
    with pytest.raises(SpecError):
        ingest_spec(b"not a spec")

def test_external_ref_rejected():
    with pytest.raises(SpecError):
        ingest_spec(b'{"openapi":"3.0.0","info":{"title":"t","version":"1"},"paths":{"/x":{"$ref":"file:///etc/passwd"}}}')

def test_yaml_alias_bomb_rejected():
    bomb = b"a: &a [1,1]\nb: &b [*a,*a]\nc: [*b,*b]\npaths: {}"
    with pytest.raises(SpecError):
        ingest_spec(bomb)  # anchors/aliases denied by the hardened loader
```

- [ ] **Step 2: Run to verify it fails** — `pytest src/recon/spec/ingest_test.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement `ingest.py`**

Load with a hardened loader that **rejects YAML anchors/aliases** (subclass `yaml.SafeLoader`, override `compose_node`/`compose_document` to raise on anchor use, or scan events and reject `AliasEvent`/anchored nodes) with a source-size cap; JSON via `json.loads`. Validate with `openapi_spec_validator` (`validate` / `OpenAPIV30SpecValidator` / `OpenAPIV2SpecValidator`) **without** registering any URL/file handler. Before/without external resolution, scan the raw structure for any `$ref` whose value is not a local `#/...` pointer → `SpecError`. Detect format from `openapi`/`swagger` keys. Resolve `servers[].url` `{var}` via `variables[var].default` (3.x) or use `basePath` (2.0). Emit `DocumentedOp(method.upper(), server_base + path)` for each path × HTTP method key. Any parse/validation failure → `SpecError`.

- [ ] **Step 4: Run to verify it passes** — `pytest src/recon/spec/ingest_test.py -v` → PASS (5).

- [ ] **Step 5: Commit**
```bash
git add src/recon/spec/__init__.py src/recon/spec/ingest.py src/recon/spec/ingest_test.py pyproject.toml
git commit -m "feat(spec): ingest + harden OpenAPI/Swagger into a documented op set" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Compare-key + partial / non-HTTP helpers

Implements spec §5.1, §5.2 (gates B3, N1, N6).

**Files:** Create `src/recon/spec/classify.py`, `src/recon/spec/classify_test.py`.

**Interfaces:**
- Consumes: `normalize.operation_of_endpoint_value`, `extract.HTTP_METHODS`.
- Produces:
  ```python
  def compare_key(operation: str) -> str          # "METHOD /a/*/c" (params wildcarded, query stripped)
  def is_partial(operation: str) -> bool           # leading/mixed ${...} or base-unresolved
  def is_non_http(operation: str) -> bool          # method not in extract.HTTP_METHODS (WS/WSS/...)
  ```

- [ ] **Step 1: Failing test**

```python
from recon.spec.classify import compare_key, is_partial, is_non_http

def test_compare_key_wildcards_all_param_styles():
    assert compare_key("GET /pets/{id}") == "GET /pets/*"
    assert compare_key("GET /pets/{petId}") == "GET /pets/*"
    assert compare_key("GET /pets/${id}") == "GET /pets/*"
    assert compare_key("GET /pets/123?x=1") == "GET /pets/*"   # query stripped, numeric wildcarded

def test_is_partial():
    assert is_partial("GET /${API}/pets") is True     # leading interpolation
    assert is_partial("GET /v${n}/pets") is True       # mixed segment
    assert is_partial("GET /pets/${id}") is False      # single-segment param -> matchable
    assert is_partial("GET /pets/{id}") is False

def test_is_non_http():
    assert is_non_http("WS /chat") is True
    assert is_non_http("GET /chat") is False
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement.** `compare_key`: split method + path (reuse `operation_of_endpoint_value` to drop `?query`); for each path segment, if it is `{...}`, a bare `${...}` (single-segment), numeric, uuid, or a `{id}/{uuid}/{hash}` template → `*`, else keep literal. `is_partial`: True if the path has a leading `${...}` segment or any segment that mixes a literal with `${...}` (contains `${` but is not exactly `${...}`). `is_non_http`: method not in `extract.HTTP_METHODS`.

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: Commit**
```bash
git add src/recon/spec/classify.py src/recon/spec/classify_test.py
git commit -m "feat(spec): compare-key, partial and non-HTTP detection" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Classify decision order

Implements spec §5.3 (gate B2). The seven-branch, suffix-before-shadow classifier.

**Files:** Modify `src/recon/spec/classify.py`, `classify_test.py`.

**Interfaces:**
- Consumes: `DocumentedOp` (Task 4), `compare_key`/`is_partial`/`is_non_http` (Task 5).
- Produces:
  ```python
  @dataclass(frozen=True)
  class Classification: status: str; reason: str; matched_operation: str | None
  def classify_operation(operation: str, documented: Sequence[DocumentedOp]) -> Classification
  ```

- [ ] **Step 1: Failing test** (covers every branch + the B2 worked example)

```python
from recon.spec.ingest import DocumentedOp
from recon.spec.classify import classify_operation as C

DOC = [DocumentedOp("GET", "/location/address/search"), DocumentedOp("POST", "/search"),
       DocumentedOp("GET", "/pets/{petId}")]

def test_documented_exact_and_param():
    assert C("GET /location/address/search", DOC).status == "documented"
    assert C("GET /pets/${id}", DOC).status == "documented"     # N1

def test_non_http_never_shadow():
    assert C("WS /chat", DOC) == __import__("recon.spec.classify", fromlist=["Classification"]).Classification("unresolved", "non-http", None)

def test_partial_never_shadow():
    assert C("GET /${API}/pets", DOC).status == "unresolved"

def test_suffix_before_verb_mismatch():   # B2: /search is a proper suffix of /location/address/search
    r = C("GET /search", DOC)
    assert r.status == "unresolved" and r.reason == "suffix-verify"

def test_undocumented_path_is_shadow():
    r = C("DELETE /admin/wipe", DOC)
    assert r.status == "shadow" and r.reason == "undocumented-path"

def test_verb_mismatch_shadow_when_not_suffix():
    r = C("DELETE /pets/9", DOC)   # path matches GET /pets/{petId}, method differs, not a suffix case
    assert r.status == "shadow" and r.reason == "undocumented-method"
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement `classify_operation`** in the §5.3 order: (1) `is_non_http` → unresolved/non-http; (2) `is_partial` → unresolved/partial; (3) exact `compare_key` match (method+path) → documented (matched = the doc op); (4) proper-suffix match of wildcarded paths in either direction (path segments, strictly shorter/longer, not equal) → unresolved/suffix-verify (matched = the doc op); (5) same wildcard path, different method → shadow/undocumented-method; (6) else if not partial → shadow/undocumented-path; (7) else unresolved.

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: Commit**
```bash
git add src/recon/spec/classify.py src/recon/spec/classify_test.py
git commit -m "feat(spec): three-bucket classify, suffix-verify before shadow" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Summary + self-audit metric

Implements spec §5.4, §6.4 summary (gate N7).

**Files:** Modify `src/recon/spec/classify.py`, `classify_test.py`.

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class SpecSummary: documented: int; shadow: int; unresolved: int; suffix_shadow_ratio: float
  def summarize(classifications: Iterable[Classification]) -> SpecSummary
  ```

- [ ] **Step 1: Failing test**

```python
from recon.spec.classify import summarize, Classification
def test_summary_counts_and_ratio():
    cs = [Classification("shadow","undocumented-path",None),
          Classification("shadow","suffix-verify",None),   # (a shadow that is suffix-flagged for the ratio)
          Classification("documented","",None),
          Classification("unresolved","partial",None)]
    s = summarize(cs)
    assert (s.documented, s.shadow, s.unresolved) == (1, 2, 1)
    assert 0.0 <= s.suffix_shadow_ratio <= 1.0
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** counts per status; `suffix_shadow_ratio` = shadows whose reason indicates a suffix relationship ÷ total shadows (0.0 if none). Non-HTTP already excluded (they are `unresolved`).
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit**
```bash
git add src/recon/spec/classify.py src/recon/spec/classify_test.py
git commit -m "feat(spec): per-run summary + suffix self-audit ratio" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 8: Persistence + classify service

Implements spec §6.3. Store blob, upsert `session_spec`, query the session's endpoint findings, classify, upsert `finding_spec_status`, emit `spec.classified`.

**Files:** Create `src/recon/spec/service.py`, `service_test.py`.

**Interfaces:**
- Consumes: `storage.put_blob`, `ingest.ingest_spec`, `classify.classify_operation`/`summarize`, `normalize.operation_of_endpoint_value`, `events.log.record_event`, models.
- Produces:
  ```python
  def attach_and_classify(tenant_id: str, run_id: str, raw_spec: bytes) -> SpecSummary | None   # None if run invisible
  def reclassify_run(tenant_id: str, run_id: str) -> SpecSummary | None                          # no-op (None) if no session_spec
  ```

- [ ] **Step 1: Failing test** (uses the DB fixtures from `conftest.py`; mirror `probe/triage_test.py` setup for a run + a session + endpoint findings)

```python
def test_attach_classifies_endpoint_findings(seeded_run_with_endpoints):  # fixture: run + GET /location/address/search finding
    from recon.spec import service
    summary = service.attach_and_classify(TENANT, RUN_ID, OPENAPI_WITH_LOCATION)
    assert summary.documented >= 1
    # a finding_spec_status row exists for the finding, keyed by (session, hash)
    ...
def test_reclassify_noop_without_session_spec(seeded_run_with_endpoints):
    from recon.spec import service
    assert service.reclassify_run(TENANT, RUN_ID) is None
def test_reattach_retags(seeded_run_with_endpoints):
    ...  # attach spec A, then spec B -> statuses reflect B, spec_ref updated
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** `attach_and_classify`: open `tenant_session`; load run (None→return None); `spec_ref = storage.put_blob(tenant, run_id, "spec", raw_spec)`; `ingested = ingest_spec(raw_spec)`; upsert `session_spec` (on_conflict `session_id`) with format/server_bases/operation_count/spec_ref; call an internal `_classify_session(session, session_id, spec_ref, ingested)`. `_classify_session`: select distinct `Finding` where `run_id in (session's runs)` and `type == 'endpoint'` (join via `Run.session_id`), build the operation from `finding.value`, `classify_operation`; upsert `finding_spec_status` on `(session_id, finding_hash)` with the `probe/triage.py:59-74` pattern; `record_event(session, ..., "spec.classified", {counts})`; return `summarize(...)`. `reclassify_run`: if a `session_spec` exists for the run's session, re-run `_classify_session` from the stored `spec_ref` blob; else return None.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit**
```bash
git add src/recon/spec/service.py src/recon/spec/service_test.py
git commit -m "feat(spec): attach + classify service (session-scoped, idempotent)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 9: Attach endpoint `POST /runs/{run_id}/spec`

Implements spec §6.3 API.

**Files:** Create `src/recon/api/spec_router.py`, `spec_router_test.py`; modify `src/recon/api/app.py` to `include_router`.

**Interfaces:** Consumes `spec.service.attach_and_classify`. Accepts an uploaded file OR a JSON/YAML text body.

- [ ] **Step 1: Failing test** (mirror `probe_router_test.py` / `upload_test.py`)

```python
def test_post_spec_classifies_and_returns_summary(client, seeded_run):
    r = client.post(f"/runs/{RUN_ID}/spec", content=OPENAPI3, headers={**TENANT_HDR, "Content-Type":"application/yaml"})
    assert r.status_code == 200 and set(r.json()) >= {"documented","shadow","unresolved","suffix_shadow_ratio"}
def test_post_spec_unknown_run_404(client):
    assert client.post("/runs/00000000-0000-0000-0000-000000000000/spec", content=b"{}", headers=TENANT_HDR).status_code == 404
def test_post_invalid_spec_422(client, seeded_run):
    assert client.post(f"/runs/{RUN_ID}/spec", content=b"nope", headers=TENANT_HDR).status_code == 422
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** a thin router: read body bytes (file or raw), `try: summary = service.attach_and_classify(...)` → `except SpecError: 422`; `summary is None` → 404; else return the summary dict. Register in `app.py`.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit**
```bash
git add src/recon/api/spec_router.py src/recon/api/spec_router_test.py src/recon/api/app.py
git commit -m "feat(api): POST /runs/{id}/spec to attach + classify a spec" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 10: Surface spec_status in the findings read

Implements spec §6.4 (incl. `unclassified`).

**Files:** Modify `src/recon/findings/queries.py`, `src/recon/api/findings_router.py`, `queries_test.py`.

**Interfaces:** Add `SpecStatusView(status, reason, matched_operation)`; `FindingView.spec_status: SpecStatusView | None`; `FindingsView.spec_summary: SpecSummary | None`.

- [ ] **Step 1: Failing test**

```python
def test_list_findings_includes_spec_status(seeded_classified_run):
    view = queries.list_findings(TENANT, RUN_ID)
    fv = next(f for f in view.findings if f.type == "endpoint")
    assert fv.spec_status is not None and fv.spec_status.status in {"documented","shadow","unresolved"}
def test_unclassified_when_no_row(seeded_run_with_endpoints_no_spec):
    view = queries.list_findings(TENANT, RUN_ID)
    assert all(f.spec_status is None for f in view.findings)  # router renders None -> "unclassified"
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** In `list_findings`, build `spec_status_by_hash` exactly like `triage_by_hash` (`queries.py:109-114`) selecting `FindingSpecStatus` by `session_id`; thread into `_finding_view`; compute a run-scoped `spec_summary` from the classified endpoint findings (or `None` if no `session_spec`). In `findings_router.py`, add `"spec_status"` per finding (`None` → the FE shows `unclassified`) and a top-level `"spec"` block from `spec_summary`.
- [ ] **Step 4: Run → PASS** (`pytest src/recon/findings/queries_test.py src/recon/api -k findings -v`).
- [ ] **Step 5: Commit**
```bash
git add src/recon/findings/queries.py src/recon/api/findings_router.py src/recon/findings/queries_test.py
git commit -m "feat(findings): surface per-finding spec_status + run summary" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 11: Auto-reclassify at analyze-finalize

Implements spec §6.3(b), REQ-D5 (gate N3).

**Files:** Modify `src/recon/runs/coordinator.py:119-136` (`advance`); test `src/recon/runs/coordinator_completeness_test.py`.

**Interfaces:** Consumes `spec.service.reclassify_run`.

- [ ] **Step 1: Failing test**

```python
def test_advance_reclassifies_when_session_spec_present(monkeypatch, finalizing_run_with_spec):
    called = {}
    monkeypatch.setattr("recon.spec.service.reclassify_run", lambda t, r: called.setdefault("hit", (t, r)))
    coordinator.advance(redis, tenant_id=TENANT, run_id=RUN_ID, completed=RunStage.CORRELATING)
    assert called["hit"] == (TENANT, RUN_ID)
def test_advance_no_reclassify_without_spec(monkeypatch, finalizing_run_no_spec):
    ...  # reclassify_run returns None; advance still finalizes normally
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** In `advance`, after the successful `service.transition(...)` finalize block, call `spec.service.reclassify_run(tenant_id, run_id)` inside a `try/except` that swallows non-fatal errors (classification must never fail a run's finalize). Import lazily to avoid a cycle if needed.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit**
```bash
git add src/recon/runs/coordinator.py src/recon/runs/coordinator_completeness_test.py
git commit -m "feat(runs): auto-reclassify findings at finalize when a spec is attached" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 12: UI — spec upload, status chip, shadow filter

Implements spec §6.4 UI.

**Files:** Modify the findings view components under `web/src/` (mirror the existing `FindingsView`/`TriageControls` components + their Vitest specs).

**Interfaces:** Consumes the `spec_status` per-finding field, the `spec` summary block, and `POST /runs/{id}/spec` from Tasks 9–10.

- [ ] **Step 1: Write failing Vitest specs** — a `SpecUpload` control posts the file to `/runs/:id/spec` and shows the returned bucket summary; a finding row renders a `spec_status` chip (documented/shadow/unresolved/unclassified); a "shadow only" filter toggles the list.
- [ ] **Step 2: Run → FAIL** (`cd web; npm run test -- --run`).
- [ ] **Step 3: Implement** the upload control, the chip (reuse the triage-chip styling), and the client-side filter (filter in JS over the already-fetched findings; no new fetch).
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit**
```bash
git add web/src
git commit -m "feat(web): spec upload, spec_status chips, shadow filter" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Notes for the executor

- **Order:** Tasks 1→11 are backend and strictly ordered by dependency (3 before 8/10/11; 4→5→6→7 before 8; 8 before 9/11; 10 after 8). Task 12 (UI) needs 9–10.
- **Phase-1-only checkpoint:** after Task 2, the extractor already recovers base-joined paths (independently valuable) — a natural review/merge point if you want to land Phase 1 first.
- **After all tasks:** run the full host lane `./.venv/Scripts/python.exe -m pytest -m "not integration"` (expect the prior 217 still green + the new tests) and the §4 gate-2 higher-model whole-branch code review before merge.
- Confirm `openapi-spec-validator`'s current validator entrypoints via context7 before Task 4 (its API has shifted across versions).
