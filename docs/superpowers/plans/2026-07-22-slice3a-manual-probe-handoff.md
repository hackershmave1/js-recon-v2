# Slice 3a — manual-probe handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reconstruct a probeable HTTP request from a run's findings, serialize it to curl + raw-HTTP export artifacts, and record finding-level mark-confirmed/triage that survives re-runs.

**Architecture:** Additive read + small triage write over the existing slice-2 findings. Reconstruction is on-demand at read time (a pure grouping over the existing `FindingView` read model, no new stage/table). One new table, `finding_triage`, keyed `(session_id, finding_hash)`. New `recon/probe/` module + one API router. No worker/queue/stage changes.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.0, Alembic, Postgres (RLS), Redis. `pytest` (colocated `*_test.py`).

Design source: `docs/superpowers/specs/2026-07-22-slice3a-manual-probe-handoff-design.md`.

## Global Constraints

- **Package root:** `src/recon`; `pythonpath = ["src"]`, tests are `*_test.py` colocated with source.
- **Test runner:** `./.venv/Scripts/python.exe -m pytest`. Pure/unit tests: target the file directly (no infra). Integration tests carry `pytestmark = pytest.mark.integration` and need live infra — start it once with `docker compose up -d` (Postgres + Redis + MinIO); the `migrated` session fixture applies `alembic upgrade head` automatically for integration-marked tests.
- **Tenancy (REQ-S1):** every tenant-scoped table has FORCE row-level security + a `tenant_isolation` policy and an explicit GRANT to role `recon_app`; the app connects as the non-superuser role. Use `tenant_session(tenant_id)` for all app reads/writes.
- **Migrations:** mirror `0002_findings` — `Base.metadata.create_all(bind)` (idempotent) then enable+FORCE RLS + DROP/CREATE policy + GRANT, looping a `*_TABLES` tuple in `db/models.py`. (Same benign `create_all`-from-live-metadata seam as `0003`; a new *table* is safe on fresh and existing DBs.)
- **Honesty (REQ-C2):** never invent request values. Unknown path/body values render as explicit placeholders (`<name>`), never guessed.
- **Injection safety:** analyzed JS is attacker-influenced and artifacts are pasted into a shell (curl) / HTTP client (raw). curl serialization must `shlex.quote` every interpolated value; raw-HTTP serialization must strip CR/LF/control chars from every interpolated component; both cap oversized URL/body.
- **Naming (§7):** verb-noun functions, no abbreviations, business-domain names.
- **Commits:** Conventional Commits, isolated per task, on `main`. Do not push (the user pushes explicitly).

---

### Task 1: Operation-parsing helpers in `normalize.py`

Two pure helpers that recover an operation key (`METHOD + templated path`) from a *stored* finding value — the inverse of the existing `endpoint_operation` / `normalize_param_value` builders. These are the grouping key for reconstruction.

**Files:**
- Modify: `src/recon/findings/normalize.py`
- Test: `src/recon/findings/normalize_test.py`

**Interfaces:**
- Produces: `operation_of_endpoint_value(value: str) -> str`, `operation_of_param_value(value: str) -> str`

- [ ] **Step 1: Write the failing tests**

Append to `src/recon/findings/normalize_test.py`:

```python
def test_operation_of_endpoint_value_strips_query():
    assert normalize.operation_of_endpoint_value("POST /api/users/{id}?a&b") == "POST /api/users/{id}"


def test_operation_of_endpoint_value_without_query():
    assert normalize.operation_of_endpoint_value("GET /orders") == "GET /orders"


def test_operation_of_param_value_strips_location_and_name():
    assert normalize.operation_of_param_value("POST /api/users/{id} body:name") == "POST /api/users/{id}"


def test_operation_helpers_roundtrip_from_builders():
    operation = normalize.endpoint_operation("POST", "https://api.acme.io/api/users/42")
    endpoint_value = normalize.normalize_endpoint(
        "POST", "https://api.acme.io/api/users/42?a=1"
    ).value
    param_value = normalize.normalize_param_value(operation, "body", "name")
    assert normalize.operation_of_endpoint_value(endpoint_value) == operation
    assert normalize.operation_of_param_value(param_value) == operation
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/findings/normalize_test.py -k operation_of -v`
Expected: FAIL with `AttributeError: module 'recon.findings.normalize' has no attribute 'operation_of_endpoint_value'`

- [ ] **Step 3: Implement the helpers**

Add to `src/recon/findings/normalize.py`, just after `normalize_param_value`:

```python
def operation_of_endpoint_value(value: str) -> str:
    """The operation (`METHOD + templated path`) of a stored ENDPOINT finding value.

    An endpoint value is ``operation`` + an optional ``?query`` suffix
    (see :func:`normalize_endpoint`), so the operation is everything before ``?``."""
    return value.split("?", 1)[0]


def operation_of_param_value(value: str) -> str:
    """The operation of a stored PARAM finding value.

    A param value is ``f"{operation} {location}:{name}"`` (see
    :func:`normalize_param_value`); ``location:name`` is the final space-separated
    token and contains no space, so the operation is everything before it."""
    return value.rsplit(" ", 1)[0]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/findings/normalize_test.py -k operation_of -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/recon/findings/normalize.py src/recon/findings/normalize_test.py
git commit -m "feat(probe): add operation-key parsers to normalize (slice 3a)"
```

---

### Task 2: `ReconstructedRequest` + pure grouping (`reconstruct.build_requests`)

The pure heart of reconstruction: group a run's `FindingView`s by operation, union params, collect hosts, seed a concrete example URL. No DB here (unit-testable).

**Files:**
- Create: `src/recon/probe/__init__.py` (empty)
- Create: `src/recon/probe/reconstruct.py`
- Test: `src/recon/probe/reconstruct_test.py`

**Interfaces:**
- Consumes: `recon.findings.queries.FindingView` / `OccurrenceView` (existing read model); `normalize.operation_of_endpoint_value` / `operation_of_param_value` (Task 1).
- Produces: `QueryParam(name: str, example: str | None)`; `ReconstructedRequest(operation, method, path, hosts: tuple[str,...], query_params: tuple[QueryParam,...], body_params: tuple[str,...], content_type: str | None, example_url: str | None, probeable: bool, endpoint_hash: str)`; `build_requests(findings: list[FindingView]) -> list[ReconstructedRequest]`.

- [ ] **Step 1: Write the failing tests**

Create `src/recon/probe/reconstruct_test.py`:

```python
from recon.findings.queries import FindingView, OccurrenceView
from recon.probe import reconstruct


def _occ(host=None, raw_url=None):
    return OccurrenceView(
        host=host, raw_url=raw_url, source_path=None, line=1, col=1,
        offset_start=0, offset_end=1, evidence=None, engine="vespasian",
        confidence=None, verified=None,
    )


def _endpoint(value, *, host=None, raw_url=None, finding_hash="e1"):
    method = value.split(" ", 1)[0]
    return FindingView(
        finding_hash=finding_hash, type="endpoint", value=value, path="input.js",
        severity=None, attributes={"method": method, "kind": "fetch"},
        first_stage="analyzing", occurrences=[_occ(host=host, raw_url=raw_url)],
    )


def _param(value, location, name, finding_hash="p1"):
    return FindingView(
        finding_hash=finding_hash, type="param", value=value, path="input.js",
        severity=None, attributes={"location": location, "name": name},
        first_stage="analyzing", occurrences=[],
    )


def test_build_groups_endpoint_with_its_params_by_operation():
    findings = [
        _endpoint("POST /api/users/{id}", host="api.acme.io", raw_url="/api/users/42"),
        _param("POST /api/users/{id} body:name", "body", "name"),
        _param("POST /api/users/{id} query:trace", "query", "trace"),
    ]
    reqs = reconstruct.build_requests(findings)
    assert len(reqs) == 1
    req = reqs[0]
    assert req.method == "POST"
    assert req.path == "/api/users/{id}"
    assert req.hosts == ("api.acme.io",)
    assert req.body_params == ("name",)
    assert [q.name for q in req.query_params] == ["trace"]
    assert req.content_type == "application/json"
    assert req.example_url == "/api/users/42"
    assert req.probeable is True
    assert req.endpoint_hash == "e1"


def test_build_seeds_query_example_from_raw_url():
    findings = [
        _endpoint("GET /search", host="api.acme.io", raw_url="/search?q=shoes"),
        _param("GET /search query:q", "query", "q"),
    ]
    (req,) = reconstruct.build_requests(findings)
    assert req.query_params[0].name == "q"
    assert req.query_params[0].example == "shoes"


def test_build_unions_hosts_across_occurrences():
    findings = [
        _endpoint("GET /a", host="one.acme.io", raw_url="https://one.acme.io/a", finding_hash="e1"),
        _endpoint("GET /a", host="two.acme.io", raw_url="https://two.acme.io/a", finding_hash="e2"),
    ]
    (req,) = reconstruct.build_requests(findings)
    assert req.hosts == ("one.acme.io", "two.acme.io")


def test_build_marks_websocket_not_probeable():
    findings = [_endpoint("WSS /socket", host="api.acme.io", raw_url="wss://api.acme.io/socket")]
    (req,) = reconstruct.build_requests(findings)
    assert req.probeable is False


def test_build_endpoint_without_params_has_no_body():
    findings = [_endpoint("GET /ping", host="api.acme.io", raw_url="/ping")]
    (req,) = reconstruct.build_requests(findings)
    assert req.body_params == ()
    assert req.content_type is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/probe/reconstruct_test.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recon.probe'`

- [ ] **Step 3: Implement the module**

Create `src/recon/probe/__init__.py` (empty file).

Create `src/recon/probe/reconstruct.py`:

```python
"""Reconstruct a probeable request from a run's findings (REQ-P1).

On-demand at read time: group findings by operation key (METHOD + templated
path), union their params, collect candidate hosts, and keep a concrete example
URL so the artifact is ready-to-fire. Pure over the ``findings.queries`` read
model — no DB access here (that is :func:`reconstruct_run`, added later).

Honesty (REQ-C2): values we did not observe (path variables, body values) are
never invented; the serializer renders them as explicit ``<name>`` placeholders.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

from recon.findings import normalize
from recon.findings.queries import FindingView

# WebSocket "endpoints" are not HTTP requests, so curl/raw-HTTP do not apply.
_WEBSOCKET_METHODS = frozenset({"WS", "WSS"})


@dataclass(frozen=True)
class QueryParam:
    name: str
    example: str | None = None


@dataclass(frozen=True)
class ReconstructedRequest:
    operation: str          # METHOD + templated path (the grouping key)
    method: str
    path: str               # templated path
    hosts: tuple[str, ...]  # distinct occurrence hosts; may be empty (relative URL)
    query_params: tuple[QueryParam, ...]
    body_params: tuple[str, ...]
    content_type: str | None
    example_url: str | None  # a representative concrete occurrence.raw_url
    probeable: bool          # False for websocket operations
    endpoint_hash: str       # the finding_hash to triage / mark confirmed


def _method_and_path(operation: str) -> tuple[str, str]:
    method, _sep, path = operation.partition(" ")
    return method, path or "/"


def build_requests(findings: list[FindingView]) -> list[ReconstructedRequest]:
    """Group endpoint + param findings into one request per operation."""
    endpoints: dict[str, list[FindingView]] = {}
    params: dict[str, list[FindingView]] = {}
    for finding in findings:
        if finding.type == "endpoint":
            key = normalize.operation_of_endpoint_value(finding.value)
            endpoints.setdefault(key, []).append(finding)
        elif finding.type == "param":
            key = normalize.operation_of_param_value(finding.value)
            params.setdefault(key, []).append(finding)

    requests: list[ReconstructedRequest] = []
    for operation in sorted(endpoints):
        endpoint_findings = endpoints[operation]
        method, path = _method_and_path(operation)
        hosts = tuple(sorted({
            occurrence.host
            for finding in endpoint_findings
            for occurrence in finding.occurrences
            if occurrence.host
        }))
        example_url = next(
            (
                occurrence.raw_url
                for finding in endpoint_findings
                for occurrence in finding.occurrences
                if occurrence.raw_url
            ),
            None,
        )
        example_query = dict(parse_qsl(urlsplit(example_url).query)) if example_url else {}

        query_params: dict[str, QueryParam] = {}
        body_params: list[str] = []
        for param in params.get(operation, []):
            location = param.attributes.get("location")
            name = param.attributes.get("name")
            if not name:
                continue
            if location == "query" and name not in query_params:
                query_params[name] = QueryParam(name=name, example=example_query.get(name))
            elif location == "body" and name not in body_params:
                body_params.append(name)

        requests.append(
            ReconstructedRequest(
                operation=operation,
                method=method,
                path=path,
                hosts=hosts,
                query_params=tuple(query_params.values()),
                body_params=tuple(body_params),
                content_type="application/json" if body_params else None,
                example_url=example_url,
                probeable=method not in _WEBSOCKET_METHODS,
                endpoint_hash=endpoint_findings[0].finding_hash,
            )
        )
    return requests
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/probe/reconstruct_test.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/recon/probe/__init__.py src/recon/probe/reconstruct.py src/recon/probe/reconstruct_test.py
git commit -m "feat(probe): group findings into per-operation reconstructed requests (slice 3a)"
```

---

### Task 3: Serializers (`serialize.to_curl` / `to_http`) with injection safety

Pure serialization of one `ReconstructedRequest` to curl + raw HTTP. This is the security-sharp task: hostile values must be neutralized.

**Files:**
- Create: `src/recon/probe/serialize.py`
- Test: `src/recon/probe/serialize_test.py`

**Interfaces:**
- Consumes: `reconstruct.ReconstructedRequest` (Task 2).
- Produces: `to_curl(req: ReconstructedRequest) -> str | None`, `to_http(req: ReconstructedRequest) -> str | None` (both `None` when `not req.probeable`).

- [ ] **Step 1: Write the failing tests**

Create `src/recon/probe/serialize_test.py`:

```python
from recon.probe import serialize
from recon.probe.reconstruct import QueryParam, ReconstructedRequest


def _req(**overrides):
    base = dict(
        operation="POST /api/users/{id}", method="POST", path="/api/users/{id}",
        hosts=("api.acme.io",), query_params=(), body_params=("amount",),
        content_type="application/json", example_url="/api/users/123",
        probeable=True, endpoint_hash="e1",
    )
    base.update(overrides)
    return ReconstructedRequest(**base)


def test_curl_uses_concrete_example_url_and_method():
    out = serialize.to_curl(_req())
    assert "curl -X POST" in out
    assert "'https://api.acme.io/api/users/123'" in out
    assert "-H 'Content-Type: application/json'" in out
    assert '--data \'{"amount":"<amount>"}\'' in out
    assert "# add auth/headers here" in out


def test_curl_falls_back_to_base_url_placeholder_when_no_host():
    out = serialize.to_curl(_req(hosts=(), example_url="/x"))
    assert "{{base_url}}/x" in out


def test_curl_shell_quotes_hostile_url():
    # A hostile path must be quoted as a single shell token, never executed.
    out = serialize.to_curl(_req(example_url="/a; rm -rf /", body_params=(), content_type=None))
    assert "'https://api.acme.io/a; rm -rf /'" in out


def test_http_strips_crlf_injection_from_target():
    out = serialize.to_http(_req(example_url="/a\r\nX-Evil: 1", body_params=(), content_type=None))
    assert "\r" not in out
    assert "\nX-Evil:" not in out  # the injected header never became its own line


def test_websocket_request_has_no_artifacts():
    req = _req(operation="WSS /socket", method="WSS", path="/socket", probeable=False)
    assert serialize.to_curl(req) is None
    assert serialize.to_http(req) is None


def test_http_has_request_line_host_and_json_body():
    out = serialize.to_http(_req())
    assert out.startswith("POST /api/users/123 HTTP/1.1")
    assert "Host: api.acme.io" in out
    assert "Content-Type: application/json" in out
    assert '{"amount":"<amount>"}' in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/probe/serialize_test.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recon.probe.serialize'`

- [ ] **Step 3: Implement the serializers**

Create `src/recon/probe/serialize.py`:

```python
"""Serialize a ReconstructedRequest to ready-to-fire artifacts (REQ-P1).

curl and raw HTTP are the slice-3a formats (raw HTTP covers the Burp Repeater
paste workflow). Both are pure functions over one request.

Security: the analyzed JS is attacker-influenced and these artifacts are pasted
into a shell (curl) or an HTTP client (raw HTTP). So curl shell-quotes every
interpolated value and raw HTTP strips CR/LF/control chars from every component —
neither artifact may become a shell-injection or header-injection vector.
"""

from __future__ import annotations

import json
import shlex

from recon.probe.reconstruct import ReconstructedRequest

_MAX_URL = 8192
_MAX_BODY = 65536
_BASE_URL_PLACEHOLDER = "{{base_url}}"


def _control_free(text: str) -> str:
    """Drop control characters (< 0x20 and DEL) — the anti-injection primitive."""
    return "".join(ch for ch in text if 0x20 <= ord(ch) != 0x7f)


def _base_url(request: ReconstructedRequest) -> str:
    if request.hosts:
        return f"https://{_control_free(request.hosts[0])}"
    return _BASE_URL_PLACEHOLDER


def _target(request: ReconstructedRequest) -> str:
    # Prefer the concrete observed URL (ready-to-fire); fall back to templated path.
    return _control_free(request.example_url or request.path)[:_MAX_URL] or "/"


def _json_body(request: ReconstructedRequest) -> str | None:
    if not request.body_params:
        return None
    body = {name: f"<{name}>" for name in request.body_params}
    return json.dumps(body, separators=(",", ":"))[:_MAX_BODY]


def to_curl(request: ReconstructedRequest) -> str | None:
    if not request.probeable:
        return None
    url = _base_url(request) + _target(request)
    host_note = f"  (host: {_control_free(request.hosts[0])})" if request.hosts else "  (host unknown)"
    lines = [
        f"# {_control_free(request.operation)}{host_note}",
        "# add auth/headers here",
    ]
    curl = f"curl -X {shlex.quote(request.method)} {shlex.quote(url)}"
    extra: list[str] = []
    if request.content_type:
        extra.append(f"-H {shlex.quote('Content-Type: ' + request.content_type)}")
    body = _json_body(request)
    if body:
        extra.append(f"--data {shlex.quote(body)}")
    if extra:
        lines.append(curl + " \\")
        for index, piece in enumerate(extra):
            lines.append("  " + piece + (" \\" if index < len(extra) - 1 else ""))
    else:
        lines.append(curl)
    if len(request.hosts) > 1:
        lines.append("# other hosts: " + ", ".join(_control_free(h) for h in request.hosts[1:]))
    return "\n".join(lines)


def to_http(request: ReconstructedRequest) -> str | None:
    if not request.probeable:
        return None
    host = _control_free(request.hosts[0]) if request.hosts else "HOST"
    lines = [
        f"{request.method} {_target(request)} HTTP/1.1",
        f"Host: {host}",
        "# add auth/headers here",
    ]
    if request.content_type:
        lines.append(f"Content-Type: {request.content_type}")
    lines.append("")
    lines.append(_json_body(request) or "")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/probe/serialize_test.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/recon/probe/serialize.py src/recon/probe/serialize_test.py
git commit -m "feat(probe): injection-safe curl + raw-HTTP serializers (slice 3a)"
```

---

### Task 4: `finding_triage` table (model + migration `0004`)

**Files:**
- Modify: `src/recon/db/models.py` (add `FindingTriage`, add `TRIAGE_TABLES`)
- Create: `src/recon/migrations/versions/0004_finding_triage.py`
- Test: `src/recon/db/triage_model_test.py`

**Interfaces:**
- Produces: `models.FindingTriage` (columns: `id, tenant_id, session_id, finding_hash, status, note, actor, created_at, updated_at`); `models.TRIAGE_TABLES = ("finding_triage",)`.

- [ ] **Step 1: Write the failing test**

Create `src/recon/db/triage_model_test.py`:

```python
import pytest

from recon.db import models
from recon.db.base import tenant_session
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


def test_finding_triage_is_tenant_isolated_by_rls():
    tenant_a = sessions_service.create_tenant("triage-a")
    tenant_b = sessions_service.create_tenant("triage-b")
    session_view = sessions_service.create_session(
        tenant_a, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    with tenant_session(tenant_a) as session:
        session.add(models.FindingTriage(
            tenant_id=tenant_a, session_id=session_view.id,
            finding_hash="h" * 64, status="confirmed",
        ))
    with tenant_session(tenant_a) as session:
        assert session.query(models.FindingTriage).count() == 1
    with tenant_session(tenant_b) as session:
        assert session.query(models.FindingTriage).count() == 0
```

- [ ] **Step 2: Run test to verify it fails**

Ensure infra up: `docker compose up -d`
Run: `./.venv/Scripts/python.exe -m pytest src/recon/db/triage_model_test.py -v`
Expected: FAIL with `AttributeError: module 'recon.db.models' has no attribute 'FindingTriage'`

- [ ] **Step 3: Add the model + table tuple**

In `src/recon/db/models.py`, add after `FindingOccurrence`:

```python
class FindingTriage(Base):
    """A user's triage verdict on a finding (REQ-P1 mark-confirmed, REQ-D1).

    Keyed by (session_id, finding_hash) — NOT by run — so a verdict set on a
    stable finding identity (REQ-D3) survives re-runs (REQ-D5 continuous rescan).
    ``finding_hash`` is intentionally not a foreign key: triage outlives any single
    run's ``finding`` rows, so the join to a finding is logical (on the hash)."""

    __tablename__ = "finding_triage"
    __table_args__ = (
        UniqueConstraint("session_id", "finding_hash", name="uq_triage_session_finding"),
        CheckConstraint(
            "status IN ('open', 'confirmed', 'dismissed')", name="ck_triage_status"
        ),
        Index("ix_triage_session", "tenant_id", "session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("session.id", ondelete="CASCADE"), nullable=False
    )
    finding_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="open")
    note: Mapped[str | None] = mapped_column(Text)
    # Best-effort supplied label until real per-user auth lands (see api.deps).
    actor: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _now_col(nullable=False)
    updated_at: Mapped[dt.datetime] = _now_col(nullable=False)
```

Add at the end of the file (after `FINDINGS_TABLES`):

```python
# Slice-3a addition, RLS-enabled by migration 0004.
TRIAGE_TABLES: tuple[str, ...] = ("finding_triage",)
```

- [ ] **Step 4: Create migration 0004**

Create `src/recon/migrations/versions/0004_finding_triage.py`:

```python
"""slice-3a finding_triage table + row-level security

Revision ID: 0004_finding_triage
Revises: 0003_run_source_map_ref
Create Date: 2026-07-22

Mark-confirmed / triage store (REQ-P1, REQ-D1). Mirrors 0002: the table is built
from the live model metadata (``create_all`` is idempotent — only what's missing),
then FORCE row-level security + the ``tenant_isolation`` policy + an explicit GRANT
are layered on (REQ-S1). Creating a *table* is safe on both a fresh DB (build here)
and an existing one; the create_all-vs-incremental seam that bit 0003 only affects
incremental column adds.
"""

from __future__ import annotations

from alembic import op

from recon.db import models
from recon.db.base import Base

revision = "0004_finding_triage"
down_revision = "0003_run_source_map_ref"
branch_labels = None
depends_on = None

APP_ROLE = "recon_app"


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind)  # idempotent: builds only the new table

    for table in models.TRIAGE_TABLES:
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
    for table in models.TRIAGE_TABLES:
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"')
    op.drop_table("finding_triage")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/db/triage_model_test.py -v`
Expected: PASS (1 passed). (The `migrated` fixture runs `alembic upgrade head`, applying 0004.)

- [ ] **Step 6: Commit**

```bash
git add src/recon/db/models.py src/recon/migrations/versions/0004_finding_triage.py src/recon/db/triage_model_test.py
git commit -m "feat(probe): finding_triage table + RLS (migration 0004, slice 3a)"
```

---

### Task 5: Triage write (`triage.set_triage_for_run`) + audit event

**Files:**
- Create: `src/recon/probe/triage.py`
- Test: `src/recon/probe/triage_test.py`

**Interfaces:**
- Consumes: `models.FindingTriage`, `models.Run` (Task 4); `recon.events.log.record_event`.
- Produces: `VALID_STATUSES: frozenset[str]`; `TriageState(status: str, note: str | None, actor: str | None, updated_at: str)`; `set_triage_for_run(tenant_id: str, run_id: str, finding_hash: str, *, status: str, note: str | None = None, actor: str | None = None) -> TriageState | None` (returns `None` when the run is invisible to the tenant; raises `ValueError` on an invalid status).

- [ ] **Step 1: Write the failing tests**

Create `src/recon/probe/triage_test.py`:

```python
import pytest

from recon.db import models
from recon.db.base import tenant_session
from recon.probe import triage
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


def _run(tenant, session_id):
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        return str(run.id)


def test_set_triage_upserts_and_emits_event():
    tenant = sessions_service.create_tenant("tri-1")
    session_view = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    run_id = _run(tenant, session_view.id)
    finding_hash = "a" * 64

    state = triage.set_triage_for_run(
        tenant, run_id, finding_hash, status="confirmed", actor="tester"
    )
    assert state.status == "confirmed"

    # A second verdict updates the same row (upsert), not a new one.
    triage.set_triage_for_run(tenant, run_id, finding_hash, status="dismissed")
    with tenant_session(tenant) as session:
        rows = session.query(models.FindingTriage).filter_by(finding_hash=finding_hash).all()
        assert len(rows) == 1 and rows[0].status == "dismissed"
        events = session.query(models.RunEvent).filter_by(type="triage.updated").all()
        assert len(events) == 2


def test_set_triage_unknown_run_returns_none():
    tenant = sessions_service.create_tenant("tri-2")
    assert triage.set_triage_for_run(
        tenant, "00000000-0000-0000-0000-000000000000", "b" * 64, status="confirmed"
    ) is None


def test_set_triage_invalid_status_raises():
    tenant = sessions_service.create_tenant("tri-3")
    session_view = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    run_id = _run(tenant, session_view.id)
    with pytest.raises(ValueError):
        triage.set_triage_for_run(tenant, run_id, "c" * 64, status="bogus")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/probe/triage_test.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recon.probe.triage'`

- [ ] **Step 3: Implement the write path**

Create `src/recon/probe/triage.py`:

```python
"""Finding triage / mark-confirmed write path (REQ-P1, REQ-D1).

A verdict is keyed (session_id, finding_hash) so it survives re-runs. The run in
the URL only provides the session scope and the event-log correlation id; the
verdict itself is engagement-scoped. Each change appends a durable ``triage.updated``
run_event (REQ-S3 audit trail)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from recon.db import models
from recon.db.base import tenant_session
from recon.events.log import record_event

VALID_STATUSES: frozenset[str] = frozenset({"open", "confirmed", "dismissed"})


@dataclass(frozen=True)
class TriageState:
    status: str
    note: str | None
    actor: str | None
    updated_at: str


def set_triage_for_run(
    tenant_id: str,
    run_id: str,
    finding_hash: str,
    *,
    status: str,
    note: str | None = None,
    actor: str | None = None,
) -> TriageState | None:
    """Upsert the verdict for (run's session, finding_hash). ``None`` if the run is
    invisible to the tenant (RLS); ``ValueError`` on an invalid status."""
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid triage status: {status!r}")

    with tenant_session(tenant_id) as session:
        run = session.get(models.Run, run_id)
        if run is None:
            return None
        session_id = str(run.session_id)

        upsert = (
            pg_insert(models.FindingTriage)
            .values(
                tenant_id=str(tenant_id),
                session_id=session_id,
                finding_hash=finding_hash,
                status=status,
                note=note,
                actor=actor,
            )
            .on_conflict_do_update(
                index_elements=["session_id", "finding_hash"],
                set_={"status": status, "note": note, "actor": actor, "updated_at": func.now()},
            )
            .returning(models.FindingTriage.updated_at)
        )
        updated_at = session.execute(upsert).scalar_one()
        record_event(
            session,
            tenant_id=str(tenant_id),
            run_id=str(run_id),
            event_type="triage.updated",
            payload={"finding_hash": finding_hash, "status": status, "actor": actor},
        )
        return TriageState(status=status, note=note, actor=actor, updated_at=updated_at.isoformat())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/probe/triage_test.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/recon/probe/triage.py src/recon/probe/triage_test.py
git commit -m "feat(probe): triage upsert + audit event, keyed by session+finding_hash (slice 3a)"
```

---

### Task 6: DB-backed reconstruction (`reconstruct.reconstruct_run`)

**Files:**
- Modify: `src/recon/probe/reconstruct.py` (add `reconstruct_run`)
- Test: `src/recon/probe/reconstruct_run_test.py`

**Interfaces:**
- Consumes: `recon.findings.queries.list_findings` (existing); `build_requests` (Task 2).
- Produces: `reconstruct_run(tenant_id: str, run_id: str) -> list[ReconstructedRequest] | None` (`None` when the run is invisible to the tenant).

- [ ] **Step 1: Write the failing test**

Create `src/recon/probe/reconstruct_run_test.py`:

```python
import pytest

from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import store
from recon.probe import reconstruct
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


def _seed_run_with_endpoint(tenant, session_id):
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        store.record_finding(
            session, tenant_id=tenant, run_id=run_id, finding_type=FindingType.ENDPOINT,
            value="POST /api/users/{id}", path="input.js",
            occurrence=store.Occurrence(host="api.acme.io", raw_url="/api/users/42"),
            attributes={"method": "POST", "kind": "fetch"}, first_stage="analyzing",
        )
        store.record_finding(
            session, tenant_id=tenant, run_id=run_id, finding_type=FindingType.PARAM,
            value="POST /api/users/{id} body:name", path="input.js",
            occurrence=store.Occurrence(host="api.acme.io", raw_url="/api/users/42"),
            attributes={"location": "body", "name": "name"}, first_stage="analyzing",
        )
        return run_id


def test_reconstruct_run_assembles_request_from_persisted_findings():
    tenant = sessions_service.create_tenant("rec-1")
    session_view = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    run_id = _seed_run_with_endpoint(tenant, session_view.id)

    requests = reconstruct.reconstruct_run(tenant, run_id)
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.hosts == ("api.acme.io",)
    assert request.body_params == ("name",)
    assert request.content_type == "application/json"
    assert request.example_url == "/api/users/42"


def test_reconstruct_run_unknown_run_returns_none():
    tenant = sessions_service.create_tenant("rec-2")
    assert reconstruct.reconstruct_run(tenant, "00000000-0000-0000-0000-000000000000") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/probe/reconstruct_run_test.py -v`
Expected: FAIL with `AttributeError: module 'recon.probe.reconstruct' has no attribute 'reconstruct_run'`

- [ ] **Step 3: Implement `reconstruct_run`**

Add to `src/recon/probe/reconstruct.py` (bottom of file):

```python
def reconstruct_run(tenant_id: str, run_id: str) -> list[ReconstructedRequest] | None:
    """Reconstruct every probeable request for a run, or ``None`` if the run is
    invisible to the tenant. Reuses the findings read model (no new query)."""
    from recon.findings import queries  # local import avoids a module-load cycle

    view = queries.list_findings(tenant_id, run_id)
    if view is None:
        return None
    return build_requests(view.findings)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/probe/reconstruct_run_test.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/recon/probe/reconstruct.py src/recon/probe/reconstruct_run_test.py
git commit -m "feat(probe): reconstruct_run reads findings and assembles requests (slice 3a)"
```

---

### Task 7: Surface triage on the findings read (`findings/queries.py` join)

**Files:**
- Modify: `src/recon/findings/queries.py` (add `TriageView`, `triage` on `FindingView`, join in `list_findings`)
- Modify: `src/recon/api/findings_router.py` (emit `triage` per finding)
- Test: `src/recon/findings/queries_triage_test.py`

**Interfaces:**
- Consumes: `models.FindingTriage` (Task 4); `models.Run.session_id`.
- Produces: `queries.TriageView(status, note, actor, updated_at)`; `FindingView.triage: TriageView | None` (default `None`, so Task-2 constructors stay valid).

- [ ] **Step 1: Write the failing test**

Create `src/recon/findings/queries_triage_test.py`:

```python
import pytest

from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import queries, store
from recon.probe import triage
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


def _run_with_endpoint(tenant, session_id):
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        result = store.record_finding(
            session, tenant_id=tenant, run_id=run_id, finding_type=FindingType.ENDPOINT,
            value="GET /orders", path="input.js",
            occurrence=store.Occurrence(host="api.acme.io", raw_url="/orders"),
            attributes={"method": "GET", "kind": "fetch"}, first_stage="analyzing",
        )
        return run_id, result.finding_hash


def test_findings_read_carries_triage_and_survives_a_rerun():
    tenant = sessions_service.create_tenant("join-1")
    session_view = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    run_one, finding_hash = _run_with_endpoint(tenant, session_view.id)

    triage.set_triage_for_run(tenant, run_one, finding_hash, status="confirmed", actor="t")

    # A NEW run in the same session re-produces the same finding_hash; the verdict
    # must still attach — proof that triage is session+hash scoped, not run scoped.
    run_two, finding_hash_two = _run_with_endpoint(tenant, session_view.id)
    assert finding_hash_two == finding_hash

    view = queries.list_findings(tenant, run_two)
    endpoint = next(f for f in view.findings if f.finding_hash == finding_hash)
    assert endpoint.triage is not None
    assert endpoint.triage.status == "confirmed"


def test_untriaged_finding_reads_as_none():
    tenant = sessions_service.create_tenant("join-2")
    session_view = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    run_id, _hash = _run_with_endpoint(tenant, session_view.id)
    view = queries.list_findings(tenant, run_id)
    assert view.findings[0].triage is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/findings/queries_triage_test.py -v`
Expected: FAIL with `AttributeError: 'FindingView' object has no attribute 'triage'`

- [ ] **Step 3: Add `TriageView`, extend `FindingView`, join in `list_findings`**

In `src/recon/findings/queries.py`:

Add the import (extend the existing models import line):

```python
from recon.db.models import Finding, FindingOccurrence, FindingTriage, Run, RunEvent
```

Add a `TriageView` dataclass (near the other view dataclasses):

```python
@dataclass(frozen=True)
class TriageView:
    status: str
    note: str | None
    actor: str | None
    updated_at: str
```

Add a defaulted `triage` field to `FindingView` (append as the last field so existing constructors keep working):

```python
    occurrences: list[OccurrenceView]
    triage: TriageView | None = None
```

Rewrite `list_findings` to fetch the run's session, load the session's triage, and pass each finding's verdict into `_finding_view`:

```python
def list_findings(tenant_id: str, run_id: str) -> FindingsView | None:
    with tenant_session(tenant_id) as session:
        run = session.get(Run, run_id)
        if run is None:
            return None
        triage_by_hash = {
            row.finding_hash: row
            for row in session.scalars(
                select(FindingTriage).where(FindingTriage.session_id == str(run.session_id))
            ).all()
        }
        findings = session.scalars(
            select(Finding)
            .where(Finding.run_id == str(run_id))
            .order_by(Finding.type, Finding.value, Finding.finding_hash)
            .options(selectinload(Finding.occurrences))
        ).all()
        return FindingsView(
            run_id=str(run_id),
            findings=[
                _finding_view(finding, triage_by_hash.get(finding.finding_hash))
                for finding in findings
            ],
            coverage=_latest_coverage(session, run_id),
        )
```

Update `_finding_view` to accept and map the triage row:

```python
def _finding_view(finding: Finding, triage_row: FindingTriage | None = None) -> FindingView:
    return FindingView(
        finding_hash=finding.finding_hash,
        type=finding.type,
        value=finding.value,
        path=finding.path,
        severity=finding.severity,
        attributes=dict(finding.attributes or {}),
        first_stage=finding.first_stage,
        occurrences=[
            _occurrence_view(occurrence)
            for occurrence in sorted(
                finding.occurrences,
                key=lambda o: (o.source_path or "", o.offset_start or 0, o.occurrence_hash),
            )
        ],
        triage=_triage_view(triage_row),
    )


def _triage_view(row: FindingTriage | None) -> TriageView | None:
    if row is None:
        return None
    return TriageView(
        status=row.status, note=row.note, actor=row.actor,
        updated_at=row.updated_at.isoformat(),
    )
```

- [ ] **Step 4: Emit `triage` in the findings router**

In `src/recon/api/findings_router.py`, add a `triage` key inside the per-finding dict (after `"first_stage": finding.first_stage,`):

```python
                "first_stage": finding.first_stage,
                "triage": (
                    None
                    if finding.triage is None
                    else {
                        "status": finding.triage.status,
                        "note": finding.triage.note,
                        "actor": finding.triage.actor,
                        "updated_at": finding.triage.updated_at,
                    }
                ),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/findings/queries_triage_test.py -v`
Expected: PASS (2 passed)

Also run the existing findings suite to confirm the additive change didn't break it:

Run: `./.venv/Scripts/python.exe -m pytest src/recon/findings/queries_test.py -v`
Expected: PASS (unchanged)

- [ ] **Step 6: Commit**

```bash
git add src/recon/findings/queries.py src/recon/api/findings_router.py src/recon/findings/queries_triage_test.py
git commit -m "feat(probe): surface session-scoped triage on the findings read (slice 3a)"
```

---

### Task 8: API router (`GET /runs/{id}/requests`, `POST …/triage`) + app wiring

**Files:**
- Create: `src/recon/api/probe_router.py`
- Modify: `src/recon/api/app.py` (register the router)
- Test: `src/recon/api/probe_router_test.py`

**Interfaces:**
- Consumes: `reconstruct.reconstruct_run` (Task 6), `serialize.to_curl`/`to_http` (Task 3), `triage.set_triage_for_run`/`VALID_STATUSES` (Task 5), `get_tenant_id` (existing).
- Produces: routes `GET /runs/{run_id}/requests` and `POST /runs/{run_id}/findings/{finding_hash}/triage`.

- [ ] **Step 1: Write the failing tests**

Create `src/recon/api/probe_router_test.py`:

```python
import pytest
from fastapi.testclient import TestClient

from recon.api.app import create_app
from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import store
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


@pytest.fixture()
def client():
    return TestClient(create_app())


def _headers(tenant):
    return {"X-Tenant-Id": tenant}


def _seed(tenant, session_id):
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        result = store.record_finding(
            session, tenant_id=tenant, run_id=run_id, finding_type=FindingType.ENDPOINT,
            value="POST /api/users/{id}", path="input.js",
            occurrence=store.Occurrence(host="api.acme.io", raw_url="/api/users/42"),
            attributes={"method": "POST", "kind": "fetch"}, first_stage="analyzing",
        )
        return run_id, result.finding_hash


def test_get_requests_returns_artifacts(client, authorized_session):
    tenant, session_id = authorized_session
    run_id, _hash = _seed(tenant, session_id)
    resp = client.get(f"/runs/{run_id}/requests", headers=_headers(tenant))
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    request = body["requests"][0]
    assert request["method"] == "POST"
    assert "curl -X POST" in request["artifacts"]["curl"]
    assert request["artifacts"]["http"].startswith("POST /api/users/42 HTTP/1.1")


def test_get_requests_unknown_run_is_404(client, tenant):
    resp = client.get(
        "/runs/00000000-0000-0000-0000-000000000000/requests", headers=_headers(tenant)
    )
    assert resp.status_code == 404


def test_post_triage_confirms_and_shows_on_findings(client, authorized_session):
    tenant, session_id = authorized_session
    run_id, finding_hash = _seed(tenant, session_id)
    resp = client.post(
        f"/runs/{run_id}/findings/{finding_hash}/triage",
        json={"status": "confirmed", "actor": "tester"},
        headers=_headers(tenant),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"

    findings = client.get(f"/runs/{run_id}/findings", headers=_headers(tenant)).json()
    endpoint = next(f for f in findings["findings"] if f["finding_hash"] == finding_hash)
    assert endpoint["triage"]["status"] == "confirmed"


def test_post_triage_bad_status_is_400(client, authorized_session):
    tenant, session_id = authorized_session
    run_id, finding_hash = _seed(tenant, session_id)
    resp = client.post(
        f"/runs/{run_id}/findings/{finding_hash}/triage",
        json={"status": "bogus"},
        headers=_headers(tenant),
    )
    assert resp.status_code == 400


def test_post_triage_unknown_run_is_404(client, tenant):
    resp = client.post(
        "/runs/00000000-0000-0000-0000-000000000000/findings/" + "a" * 64 + "/triage",
        json={"status": "confirmed"},
        headers=_headers(tenant),
    )
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/api/probe_router_test.py -v`
Expected: FAIL (404 on `/requests` — route not registered yet)

- [ ] **Step 3: Implement the router**

Create `src/recon/api/probe_router.py`:

```python
"""Manual-probe handoff endpoints (REQ-P1).

``GET /runs/{run_id}/requests`` returns each reconstructed request with inline
curl + raw-HTTP artifacts. ``POST /runs/{run_id}/findings/{finding_hash}/triage``
records a mark-confirmed / triage verdict. Both are thin: reconstruct/serialize
and the triage upsert live in ``recon.probe``. Isolation is the database's (RLS)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from recon.api.deps import get_tenant_id
from recon.probe import reconstruct, serialize, triage
from recon.probe.reconstruct import ReconstructedRequest

router = APIRouter(tags=["probe"])


class TriageRequest(BaseModel):
    status: str
    note: str | None = None
    actor: str | None = None


@router.get("/runs/{run_id}/requests")
def get_run_requests(run_id: str, tenant_id: str = Depends(get_tenant_id)) -> dict:
    requests = reconstruct.reconstruct_run(tenant_id, run_id)
    if requests is None:
        raise HTTPException(status_code=404, detail="run not found")
    return {
        "run_id": run_id,
        "count": len(requests),
        "requests": [_request_dict(request) for request in requests],
    }


@router.post("/runs/{run_id}/findings/{finding_hash}/triage")
def set_finding_triage(
    run_id: str,
    finding_hash: str,
    body: TriageRequest,
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    if body.status not in triage.VALID_STATUSES:
        raise HTTPException(status_code=400, detail="invalid triage status")
    state = triage.set_triage_for_run(
        tenant_id, run_id, finding_hash,
        status=body.status, note=body.note, actor=body.actor,
    )
    if state is None:
        raise HTTPException(status_code=404, detail="run not found")
    return {
        "finding_hash": finding_hash,
        "status": state.status,
        "note": state.note,
        "actor": state.actor,
        "updated_at": state.updated_at,
    }


def _request_dict(request: ReconstructedRequest) -> dict:
    artifacts = (
        None
        if not request.probeable
        else {"curl": serialize.to_curl(request), "http": serialize.to_http(request)}
    )
    return {
        "operation": request.operation,
        "method": request.method,
        "path": request.path,
        "hosts": list(request.hosts),
        "query_params": [{"name": q.name, "example": q.example} for q in request.query_params],
        "body_params": list(request.body_params),
        "content_type": request.content_type,
        "example_url": request.example_url,
        "probeable": request.probeable,
        "endpoint_hash": request.endpoint_hash,
        "artifacts": artifacts,
    }
```

- [ ] **Step 4: Register the router**

In `src/recon/api/app.py`, add the import and registration:

```python
from recon.api import findings_router, probe_router, runs_router, sessions_router
```

```python
    app.include_router(findings_router.router)
    app.include_router(probe_router.router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/api/probe_router_test.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Full-suite sanity + commit**

Run the whole suite against live infra to confirm nothing regressed:

Run: `./.venv/Scripts/python.exe -m pytest -m ""`
Expected: PASS (prior 206/207 + the new slice-3a tests)

```bash
git add src/recon/api/probe_router.py src/recon/api/app.py src/recon/api/probe_router_test.py
git commit -m "feat(probe): manual-probe handoff API — reconstructed requests + triage (slice 3a)"
```

---

## Review gates (after the tasks, per workflow §4)

1. **Adversarial design review** — a subagent tasked with disproving the design, backed by docs/exact code lines. Focus: serializer injection safety (shell + CRLF), reconstruction honesty (no invented values, unattributed truly excluded), triage keying (survives re-run, RLS-isolated), the no-FK-on-hash choice.
2. **Higher-model code review** — hand the full slice-3a diff to a more capable model before it's considered done.

State each verdict + evidence back to the user; fix highs before closing the slice. Then update `slice2-findings-progress` memory (or a new slice-3 memory) and the handoff.

## Self-review notes (checked against the spec)

- **Spec coverage:** §3 module layout → Tasks 2/3/5/6/8; §4 data model → Task 4 + Task 7 join; §5 reconstruction → Tasks 2/6; §6 serializers+security → Task 3; §7 API → Task 8; §8 observability/audit → `triage.updated` event in Task 5; §9 testing → tests in every task. `probe/queries.py` from the spec sketch is intentionally **not** created — its two responsibilities landed as `reconstruct_run` (Task 6) and the `findings/queries.py` triage join (Task 7), avoiding a thin pass-through module (YAGNI); this is the only deviation from the spec's file sketch.
- **Placeholder scan:** none — every code/test step carries complete code and exact commands.
- **Type consistency:** `ReconstructedRequest` / `QueryParam` (Task 2) are used unchanged by Tasks 3, 6, 8; `TriageState` (Task 5) fields match the router output (Task 8); `TriageView` (Task 7) matches the findings-router emit; `set_triage_for_run` signature is identical in Tasks 5, 7, 8.
- **Known limitation (documented, not a gap):** when one operation has multiple endpoint findings (same operation, different query sets), `endpoint_hash` is the deterministic-first; confirming it triages that finding. Typical case is one endpoint finding per operation.
