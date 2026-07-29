# REQ-C2 manual base-URL resolution — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an analyst manually set a base URL (a path-prefix rule or an explicit finding selection) and re-resolve the client findings that depend on it, across files — filling the OpenAPI export's `{{base_url}}` and moving partial cross-file paths out of the shadow classifier's `unresolved` bucket.

**Architecture:** A pure prepend-only resolver `src/recon/findings/base_url.py` applies analyst-set rules to a client operation's PATH at **read time**; the rules live in a new session-scoped `session_base_url` table (RLS, mirroring `session_spec`). The resolver is applied at the **two** client-operation sources — `probe/reconstruct.py::build_requests` (post endpoint↔param join) and `spec/service.py::_classify_session` (host-gated) — and setting a rule triggers the existing `reclassify_run`. Findings are never rewritten (identity non-churn).

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy + Alembic (Postgres, RLS), pytest (colocated `*_test.py`); React + Vite + Vitest (`web/`).

**Spec:** `docs/superpowers/specs/2026-07-29-req-c2-base-url-design.md` (branch `req-c2-base-url`, §4 gate passed at `c8e688f`).

## Global Constraints

- **Read-time overlay, never a mutation** (spec §2.2). The resolver is applied when reading (reconstruct/classify); stored `Finding` rows and `finding_hash` are never rewritten.
- **Prepend-only, segment-boundary, idempotent, relative-only** (spec §2.5, §5). Only prepend a base to a host-less/relative path; match prefixes on whole path segments; applying twice is a no-op; upholds the classifier SAFETY INVARIANT (`classify.py:170-185`).
- **Session-scoped persistence** (spec §2.4). Rules live in `session_base_url`, keyed by session like `session_spec`/`finding_spec_status`.
- **Exact RLS policy** (spec §4, gate B3), copied verbatim from `0006_spec_diff.py:38-43`: `USING (tenant_id::text = current_setting('app.current_tenant', true)) WITH CHECK (…)`, plus `GRANT SELECT, INSERT, UPDATE, DELETE … TO recon_app`. GUC is `app.current_tenant`.
- **Set-base triggers reclassify** (spec §2.6) so persisted `finding_spec_status` verdicts never go stale.
- **No active traffic / no new egress** (REQ-P1/P2) — pure static resolution over stored findings.
- **Test runner:** host lane `./.venv/Scripts/python.exe -m pytest <path> -v` (this env sometimes drops pytest's final "N passed" line — trust the exit code). Pure tests run under `-m "not integration"`; integration tests are `@pytest.mark.integration` and need the docker stack up. FE: `cd web && npm run test` (Vitest).
- **Commits:** Conventional Commits, one at the end of each task. Do **not** push (the user's call).

---

## File structure

| File | Responsibility |
|---|---|
| `src/recon/domain.py` (modify) | Add `BaseUrlRuleKind` StrEnum (`prefix`/`selection`). |
| `src/recon/db/models.py` (modify) | Add `SessionBaseUrl` model + `BASE_URL_TABLES` tuple. |
| `src/recon/migrations/versions/0007_session_base_url.py` (new) | `create_all` + RLS policy, mirroring `0006`. |
| `src/recon/findings/base_url.py` (new) | Pure: `BaseUrlRule`, `ResolvedOp`, `validate_base_url`, `resolve_operation`. |
| `src/recon/findings/base_url_test.py` (new) | Host-lane pure tests for the resolver. |
| `src/recon/findings/queries.py` (modify) | `base_url_rules_in_session(session, session_id)` + `list_base_url_rules(tenant_id, run_id)`. |
| `src/recon/probe/reconstruct.py` (modify) | `build_requests(findings, rules=())` applies the resolver post-join + collision-merge; `reconstruct_run` loads rules. |
| `src/recon/spec/service.py` (modify) | `_classify_session` host-gates + applies the resolver before `classify_operation`. |
| `src/recon/spec/base_url_service.py` (new) | `add_rule` / `list_rules` / `delete_rule`, each triggering `reclassify_run`. |
| `src/recon/api/base_url_router.py` (new) | `POST/GET/DELETE /runs/{run_id}/base-url`. |
| `src/recon/api/app.py` (modify) | Register `base_url_router` before the SPA catch-all. |
| `web/src/api/apiClient.ts` + `types.ts` (modify) | `listBaseUrlRules` / `addBaseUrlRule` / `deleteBaseUrlRule` + `BaseUrlRule` type. |
| `web/src/features/findings/BaseUrlPanel.tsx` (+ test) (new) | The analyst panel (mirror `SpecUpload.tsx`). |

**Interfaces produced (used across tasks):**
- `BaseUrlRuleKind` (StrEnum: `PREFIX="prefix"`, `SELECTION="selection"`) — Task 1.
- `SessionBaseUrl` model; `models.BASE_URL_TABLES = ("session_base_url",)` — Task 1.
- `BaseUrlRule(kind: str, base_url: str, path_prefix: str | None = None, finding_hashes: tuple[str, ...] = ())` — Task 2.
- `ResolvedOp(path: str, host: str | None, scheme: str | None, changed: bool)` — Task 2.
- `validate_base_url(base_url: str) -> None` (raises `InvalidBaseUrl(ValueError)`) — Task 2.
- `resolve_operation(method: str, path: str, endpoint_hashes: tuple[str, ...], has_host: bool, rules: list[BaseUrlRule]) -> ResolvedOp` — Task 2.
- `queries.base_url_rules_in_session(session, session_id: str) -> list[BaseUrlRule]`; `queries.list_base_url_rules(tenant_id: str, run_id: str) -> list[BaseUrlRule]` — Task 3.
- `build_requests(findings, rules: list[BaseUrlRule] = ()) -> list[ReconstructedRequest]` — Task 4 (extends the existing signature; default `()` = unchanged behavior).
- `base_url_service.add_rule/list_rules/delete_rule` — Task 6.

Already exist (verified `c8e688f`): `ReconstructedRequest(operation, method, path, hosts, query_params, body_params, content_type, example_url, probeable, endpoint_hashes)` and `QueryParam(name, example=None)` (`reconstruct.py:28-45`); `normalize.operation_of_endpoint_value` (`:238`); `classify_operation` (`classify.py:186`); `reclassify_run` (`service.py:83`); `get_tenant_id` (`deps.py:24`); `tenant_session` (`db/base.py:40`).

---

### Task 1: Persistence — enum, model, migration

Adds the `session_base_url` table (session-scoped, RLS) and its `kind` enum. Deliverable: the model imports and the table stands up under RLS.

**Files:**
- Modify: `src/recon/domain.py`
- Modify: `src/recon/db/models.py`
- Create: `src/recon/migrations/versions/0007_session_base_url.py`
- Test: `src/recon/db/session_base_url_model_test.py`

**Interfaces:**
- Consumes: `Base`, `_UUID_PK`, `_enum_check`, `_now_col` (`models.py:20-49`); `StrEnum` (`domain.py`).
- Produces: `domain.BaseUrlRuleKind`; `models.SessionBaseUrl`; `models.BASE_URL_TABLES`.

- [ ] **Step 1: Write the failing test**

```python
# src/recon/db/session_base_url_model_test.py
from sqlalchemy import CheckConstraint, UniqueConstraint

from recon.db import models
from recon.domain import BaseUrlRuleKind


def test_kind_enum_values():
    assert [m.value for m in BaseUrlRuleKind] == ["prefix", "selection"]


def test_session_base_url_table_shape():
    table = models.SessionBaseUrl.__table__
    assert table.name == "session_base_url"
    cols = set(table.columns.keys())
    assert {"id", "tenant_id", "session_id", "kind", "path_prefix",
            "finding_hashes", "base_url", "actor", "created_at", "updated_at"} <= cols
    # A unique (session_id, path_prefix) so prefix rules upsert; selection rows (NULL prefix) don't collide.
    assert any(
        isinstance(c, UniqueConstraint) and {col.name for col in c.columns} == {"session_id", "path_prefix"}
        for c in table.constraints
    )
    # The kind CHECK + the "exactly one match field per kind" CHECK both present.
    checks = [c for c in table.constraints if isinstance(c, CheckConstraint)]
    assert any(c.name == "ck_base_url_kind" for c in checks)
    assert any(c.name == "ck_base_url_match_field" for c in checks)


def test_registered_for_rls():
    assert models.BASE_URL_TABLES == ("session_base_url",)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/db/session_base_url_model_test.py -v`
Expected: FAIL — `ImportError: cannot import name 'BaseUrlRuleKind'`.

- [ ] **Step 3: Add the enum**

```python
# src/recon/domain.py — add after FindingType (around :71)
class BaseUrlRuleKind(StrEnum):
    """How a manual base-URL rule selects the findings it re-resolves (REQ-C2)."""

    PREFIX = "prefix"        # matches ops whose path starts with path_prefix (segment-wise)
    SELECTION = "selection"  # matches ops whose endpoint finding_hash is in finding_hashes
```

- [ ] **Step 4: Add the model + table tuple**

```python
# src/recon/db/models.py — import the enum (extend the existing domain import on :33)
from recon.domain import (
    AssetStatus,
    BaseUrlRuleKind,
    FindingType,
    JobState,
    QueueName,
    RunStage,
    RunState,
)

# add ARRAY to the sqlalchemy.dialects.postgresql import (:29)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

# add the model after FindingSpecStatus (around :436)
class SessionBaseUrl(Base):
    """A manual base-URL rule for a session (REQ-C2). Read-time overlay only —
    applied by recon.findings.base_url at reconstruct/classify time; findings are
    never rewritten (identity non-churn). Session-scoped like session_spec."""

    __tablename__ = "session_base_url"
    __table_args__ = (
        # Prefix rules upsert on their prefix; selection rows have NULL path_prefix
        # and (NULLS DISTINCT) never collide here.
        UniqueConstraint("session_id", "path_prefix", name="uq_base_url_session_prefix"),
        CheckConstraint(_enum_check("kind", BaseUrlRuleKind), name="ck_base_url_kind"),
        CheckConstraint(
            "(kind = 'prefix' AND path_prefix IS NOT NULL AND finding_hashes IS NULL) "
            "OR (kind = 'selection' AND finding_hashes IS NOT NULL AND path_prefix IS NULL)",
            name="ck_base_url_match_field",
        ),
        Index("ix_base_url_session", "tenant_id", "session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("session.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    path_prefix: Mapped[str | None] = mapped_column(Text)
    finding_hashes: Mapped[list | None] = mapped_column(ARRAY(Text))
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _now_col(nullable=False)
    updated_at: Mapped[dt.datetime] = _now_col(nullable=False)


# add near the other RLS table tuples (after SPEC_TABLES, :461)
# REQ-C2 manual base-URL addition, RLS-enabled by migration 0007.
BASE_URL_TABLES: tuple[str, ...] = ("session_base_url",)
```

- [ ] **Step 5: Run the model test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/db/session_base_url_model_test.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Write the migration**

```python
# src/recon/migrations/versions/0007_session_base_url.py
"""REQ-C2 manual base-URL rules (session_base_url) + RLS

Revision ID: 0007_session_base_url
Revises: 0006_spec_diff
Create Date: 2026-07-29

Mirrors 0006: a brand-new table built from live model metadata (create_all is
idempotent — only what's missing), then FORCE row-level security + the
tenant_isolation policy + an explicit GRANT (REQ-S1). No existing-table column
adds, so the create_all-vs-incremental seam that bit 0003 does not apply.
"""

from __future__ import annotations

from alembic import op

from recon.db import models
from recon.db.base import Base

revision = "0007_session_base_url"
down_revision = "0006_spec_diff"
branch_labels = None
depends_on = None

APP_ROLE = "recon_app"


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind)  # idempotent: builds only the new table

    for table in models.BASE_URL_TABLES:
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
    for table in models.BASE_URL_TABLES:
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"')
    op.drop_table("session_base_url")
```

- [ ] **Step 7: Apply the migration against the stack**

Run (stack up): `docker compose run --rm migrate`
Expected: exit 0; `session_base_url` exists with RLS. (If the stack isn't up: `docker compose up -d postgres redis minio migrate`.) The Task 5-7 integration tests also fail loudly if the table is missing.

- [ ] **Step 8: Commit**

```bash
git add src/recon/domain.py src/recon/db/models.py src/recon/migrations/versions/0007_session_base_url.py src/recon/db/session_base_url_model_test.py
git commit -m "feat(req-c2): session_base_url table, kind enum, RLS migration"
```

---

### Task 2: Pure resolver (`findings/base_url.py`)

The core read-time overlay: match one rule (selection > longest prefix), prepend the base to a host-less path on segment boundaries, idempotently. Pure, stdlib-only, no DB.

**Files:**
- Create: `src/recon/findings/base_url.py`
- Test: `src/recon/findings/base_url_test.py`

**Interfaces:**
- Consumes: nothing (pure).
- Produces: `BaseUrlRule`, `ResolvedOp`, `InvalidBaseUrl`, `validate_base_url`, `resolve_operation` (signatures in the file-structure interface list).

- [ ] **Step 1: Write the failing test**

```python
# src/recon/findings/base_url_test.py
import pytest

from recon.findings.base_url import (
    BaseUrlRule,
    InvalidBaseUrl,
    resolve_operation,
    validate_base_url,
)


def _prefix(prefix, base):
    return BaseUrlRule(kind="prefix", base_url=base, path_prefix=prefix)


def _selection(hashes, base):
    return BaseUrlRule(kind="selection", base_url=base, finding_hashes=tuple(hashes))


def test_prefix_rule_prepends_whole_path():
    r = resolve_operation("GET", "/address/search", ("h1",), False, [_prefix("/address", "/location")])
    assert r.path == "/location/address/search"
    assert r.host is None and r.changed is True


def test_selection_rule_matches_by_hash():
    r = resolve_operation("GET", "/address/search", ("h1",), False, [_selection(["h1"], "/location")])
    assert r.path == "/location/address/search" and r.changed is True


def test_selection_beats_prefix():
    rules = [_prefix("/address", "/wrong"), _selection(["h1"], "/right")]
    r = resolve_operation("GET", "/address/x", ("h1",), False, rules)
    assert r.path == "/right/address/x"


def test_longest_prefix_wins():
    rules = [_prefix("/a", "/short"), _prefix("/a/b", "/long")]
    r = resolve_operation("GET", "/a/b/c", ("h1",), False, rules)
    assert r.path == "/long/a/b/c"


def test_segment_boundary_match_only():
    # '/address' must NOT match '/address-svc/...'
    r = resolve_operation("GET", "/address-svc/x", ("h1",), False, [_prefix("/address", "/location")])
    assert r.path == "/address-svc/x" and r.changed is False


def test_absolute_op_is_not_rebased():
    # has_host True => the op already carries a resolved host; never re-base it (gate B1).
    r = resolve_operation("GET", "/location/address/search", ("h1",), True, [_prefix("/location", "/x")])
    assert r.path == "/location/address/search" and r.changed is False


def test_host_bearing_base_sets_host_and_scheme():
    r = resolve_operation("GET", "/x", ("h1",), False, [_prefix("/x", "https://api.example.com/v3")])
    assert r.path == "/v3/x"
    assert r.host == "api.example.com" and r.scheme == "https"


def test_idempotent_when_already_under_base():
    rule = _prefix("/address", "/location")
    once = resolve_operation("GET", "/address/search", ("h1",), False, [rule])
    twice = resolve_operation("GET", once.path, ("h1",), False, [rule])
    assert twice.path == once.path  # '/location/address/search' no longer matches '/address'


def test_no_matching_rule_is_unchanged():
    r = resolve_operation("GET", "/other", ("h1",), False, [_prefix("/address", "/location")])
    assert r.path == "/other" and r.changed is False


def test_validate_rejects_bad_bases():
    for bad in ["", "location", "ftp://h/x", "https:///x", "/x?y=1", "https://u:p@h/x"]:
        with pytest.raises(InvalidBaseUrl):
            validate_base_url(bad)


def test_validate_accepts_good_bases():
    for good in ["/location", "/a/b", "https://api.example.com", "http://h:8443/v3"]:
        validate_base_url(good)  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/findings/base_url_test.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'recon.findings.base_url'`.

- [ ] **Step 3: Write the implementation**

```python
# src/recon/findings/base_url.py
"""REQ-C2 manual base-URL resolution — the pure read-time overlay.

Analyst-set rules (prefix or selection) prepend a base to a host-less client
operation PATH. Prepend-only (never rewrite/truncate — upholds the classifier
SAFETY INVARIANT), segment-boundary matching, idempotent, relative-only. Pure
and stdlib-only: the DB/service layer builds ``BaseUrlRule`` from rows and passes
them in; reconstruct/classify apply the result at read time. Findings are never
rewritten, so finding identity never churns.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class BaseUrlRule:
    kind: str                              # "prefix" | "selection"
    base_url: str                          # 'https://api.example.com/v3' or '/location'
    path_prefix: str | None = None         # kind == "prefix"
    finding_hashes: tuple[str, ...] = ()   # kind == "selection"


@dataclass(frozen=True)
class ResolvedOp:
    path: str
    host: str | None      # netloc (host[:port]) if base_url carried one, else None
    scheme: str | None    # scheme if base_url carried one, else None
    changed: bool


class InvalidBaseUrl(ValueError):
    """A base_url that is neither a root-relative path nor an http(s) URL."""


def validate_base_url(base_url: str) -> None:
    """Raise :class:`InvalidBaseUrl` unless ``base_url`` is usable: a root-relative
    path (``/x``) or an absolute http(s) URL. No query/fragment, no userinfo."""
    if not base_url:
        raise InvalidBaseUrl("base_url must not be empty")
    split = urlsplit(base_url)
    if split.query or split.fragment:
        raise InvalidBaseUrl("base_url must not carry a query or fragment")
    if split.scheme or split.netloc:
        if split.scheme not in ("http", "https"):
            raise InvalidBaseUrl("base_url scheme must be http or https")
        if not split.netloc:
            raise InvalidBaseUrl("base_url with a scheme must include a host")
        if "@" in split.netloc:
            raise InvalidBaseUrl("base_url must not carry userinfo")
    elif not base_url.startswith("/"):
        raise InvalidBaseUrl("a path-only base_url must start with '/'")


def _segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s != ""]


def _is_segment_prefix(prefix: str, path: str) -> bool:
    """True if ``path`` starts with ``prefix`` on whole-segment boundaries."""
    p, q = _segments(prefix), _segments(path)
    return len(p) <= len(q) and q[: len(p)] == p


def _split_base(base_url: str) -> tuple[str | None, str | None, str]:
    """``(scheme, netloc, path_prefix)`` — scheme/netloc are ``None`` for a
    path-only base; the path has any trailing slash stripped so a join is a clean
    concat."""
    split = urlsplit(base_url)
    if split.scheme or split.netloc:
        return split.scheme, split.netloc, split.path.rstrip("/")
    return None, None, base_url.rstrip("/")


def _match(path: str, endpoint_hashes: tuple[str, ...], rules: list[BaseUrlRule]) -> BaseUrlRule | None:
    """At most one rule applies: a selection rule (explicit) beats every prefix
    rule; among prefix rules the longest (most segments) matching prefix wins."""
    hashset = set(endpoint_hashes)
    for rule in rules:
        if rule.kind == "selection" and hashset & set(rule.finding_hashes):
            return rule
    best: BaseUrlRule | None = None
    for rule in rules:
        if rule.kind == "prefix" and rule.path_prefix and _is_segment_prefix(rule.path_prefix, path):
            if best is None or len(_segments(rule.path_prefix)) > len(_segments(best.path_prefix or "")):
                best = rule
    return best


def resolve_operation(
    method: str,
    path: str,
    endpoint_hashes: tuple[str, ...],
    has_host: bool,
    rules: list[BaseUrlRule],
) -> ResolvedOp:
    """Apply the matched rule to a candidate op. Candidate = host-less
    (``has_host`` False) AND a root-relative ``path`` (begins ``/``); anything else
    is returned unchanged (no double-join, no false shadow — gate B1). Prepends the
    base path to the WHOLE op path (the prefix only selects; it is never stripped),
    idempotently (a path already under the base path is left as-is)."""
    if has_host or not path.startswith("/"):
        return ResolvedOp(path=path, host=None, scheme=None, changed=False)
    rule = _match(path, endpoint_hashes, rules)
    if rule is None:
        return ResolvedOp(path=path, host=None, scheme=None, changed=False)
    scheme, netloc, base_path = _split_base(rule.base_url)
    new_path = path
    if base_path and not _is_segment_prefix(base_path, path):
        new_path = base_path + path
    changed = new_path != path or netloc is not None
    return ResolvedOp(path=new_path, host=netloc, scheme=scheme, changed=changed)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/findings/base_url_test.py -v`
Expected: PASS (11 tests).

- [ ] **Step 5: Commit**

```bash
git add src/recon/findings/base_url.py src/recon/findings/base_url_test.py
git commit -m "feat(req-c2): pure prepend-only base-URL resolver"
```

---

### Task 3: Rule-loading queries (`findings/queries.py`)

Load a session's rules as `BaseUrlRule[]`. Two entry points: one over an open session (for `_classify_session`) and one that opens its own tenant transaction by run (for `reconstruct_run`).

**Files:**
- Modify: `src/recon/findings/queries.py`
- Test: `src/recon/findings/base_url_queries_test.py`

**Interfaces:**
- Consumes: `models.SessionBaseUrl`, `models.Run`, `tenant_session` (`db/base.py:40`), `BaseUrlRule` (Task 2).
- Produces: `base_url_rules_in_session(session, session_id: str) -> list[BaseUrlRule]`; `list_base_url_rules(tenant_id: str, run_id: str) -> list[BaseUrlRule]`.

- [ ] **Step 1: Write the failing test**

```python
# src/recon/findings/base_url_queries_test.py
import pytest

from recon.db import models
from recon.db.base import tenant_session
from recon.findings import queries

pytestmark = pytest.mark.integration


def _add_run(session, tenant, session_id):
    run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
    session.add(run)
    session.flush()
    return str(run.id)


def test_list_rules_for_run_returns_typed_rules(authorized_session):
    tenant, session_id = authorized_session
    with tenant_session(tenant) as session:
        run_id = _add_run(session, tenant, session_id)
        session.add(models.SessionBaseUrl(
            tenant_id=tenant, session_id=session_id, kind="prefix",
            path_prefix="/address", base_url="/location",
        ))
        session.add(models.SessionBaseUrl(
            tenant_id=tenant, session_id=session_id, kind="selection",
            finding_hashes=["abc"], base_url="https://api.example.com",
        ))

    rules = queries.list_base_url_rules(tenant, run_id)
    kinds = {r.kind for r in rules}
    assert kinds == {"prefix", "selection"}
    prefix = next(r for r in rules if r.kind == "prefix")
    assert prefix.path_prefix == "/address" and prefix.base_url == "/location"
    selection = next(r for r in rules if r.kind == "selection")
    assert selection.finding_hashes == ("abc",)


def test_list_rules_unknown_run_is_empty(authorized_session):
    tenant, _session_id = authorized_session
    assert queries.list_base_url_rules(tenant, "00000000-0000-0000-0000-000000000000") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run (stack up): `./.venv/Scripts/python.exe -m pytest src/recon/findings/base_url_queries_test.py -v`
Expected: FAIL — `AttributeError: module 'recon.findings.queries' has no attribute 'list_base_url_rules'`.

- [ ] **Step 3: Write the implementation**

```python
# src/recon/findings/queries.py — add imports at the top (near the existing sqlalchemy/select import)
from recon.findings.base_url import BaseUrlRule

# add these functions (module level)
def base_url_rules_in_session(session, session_id: str) -> list[BaseUrlRule]:
    """Every manual base-URL rule for a session, as pure BaseUrlRule values.
    Takes an OPEN tenant session so a caller (e.g. _classify_session) can load
    rules inside its own transaction."""
    rows = session.scalars(
        select(models.SessionBaseUrl).where(models.SessionBaseUrl.session_id == session_id)
    ).all()
    return [
        BaseUrlRule(
            kind=row.kind,
            base_url=row.base_url,
            path_prefix=row.path_prefix,
            finding_hashes=tuple(row.finding_hashes or ()),
        )
        for row in rows
    ]


def list_base_url_rules(tenant_id: str, run_id: str) -> list[BaseUrlRule]:
    """The base-URL rules for a run's session, opening a tenant transaction.
    Empty list if the run is invisible to the tenant (RLS) or does not exist."""
    with tenant_session(tenant_id) as session:
        run = session.get(models.Run, run_id)
        if run is None:
            return []
        return base_url_rules_in_session(session, str(run.session_id))
```

Note: `select` and `models` are already imported in `queries.py` (it is the findings read module); `tenant_session` is imported there too. If `tenant_session` is not yet imported, add `from recon.db.base import tenant_session`.

- [ ] **Step 4: Run test to verify it passes**

Run (stack up): `./.venv/Scripts/python.exe -m pytest src/recon/findings/base_url_queries_test.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/recon/findings/queries.py src/recon/findings/base_url_queries_test.py
git commit -m "feat(req-c2): load session base-URL rules as typed values"
```

---

### Task 4: Apply the overlay in reconstruct (post-join + collision-merge)

`build_requests` gains an optional `rules` arg. After the existing endpoint↔param grouping (so params are already attached — gate B2), the resolver is applied to each assembled request; collisions are merged order-independently. `reconstruct_run` loads the rules.

**Files:**
- Modify: `src/recon/probe/reconstruct.py`
- Test: `src/recon/probe/reconstruct_base_url_test.py`

**Interfaces:**
- Consumes: `resolve_operation`, `BaseUrlRule` (Task 2); `queries.list_base_url_rules` (Task 3).
- Produces: `build_requests(findings, rules: list[BaseUrlRule] = ())` (extended); `reconstruct_run` now applies a run's rules.

- [ ] **Step 1: Write the failing test**

```python
# src/recon/probe/reconstruct_base_url_test.py
from dataclasses import replace

from recon.findings.base_url import BaseUrlRule
from recon.probe.reconstruct import ReconstructedRequest, build_requests


def _view(finding_hash, type_, value, attributes=None, occurrences=()):
    # A tiny stand-in matching the fields build_requests reads off FindingView.
    from types import SimpleNamespace
    return SimpleNamespace(
        finding_hash=finding_hash, type=type_, value=value,
        attributes=attributes or {}, occurrences=list(occurrences),
    )


def _occ(host=None, raw_url=None):
    from types import SimpleNamespace
    return SimpleNamespace(host=host, raw_url=raw_url)


def _prefix(prefix, base):
    return BaseUrlRule(kind="prefix", base_url=base, path_prefix=prefix)


def test_prefix_rule_resolves_and_preserves_params():
    findings = [
        _view("h1", "endpoint", "GET /address/search", {"method": "GET", "kind": "fetch"}, [_occ()]),
        _view("p1", "param", "GET /address/search query:page", {"location": "query", "name": "page"}),
    ]
    (req,) = build_requests(findings, [_prefix("/address", "/location")])
    assert req.path == "/location/address/search"
    assert req.operation == "GET /location/address/search"
    assert [p.name for p in req.query_params] == ["page"]  # param survived the re-key (gate B2)


def test_absolute_op_is_not_rebased():
    findings = [
        _view("h1", "endpoint", "GET /location/address/search",
              {"method": "GET", "kind": "fetch"}, [_occ(host="api.example.com",
              raw_url="https://api.example.com/location/address/search")]),
    ]
    (req,) = build_requests(findings, [_prefix("/location", "/wrong")])
    assert req.path == "/location/address/search"  # has a host -> candidate gate skips it


def test_host_bearing_base_sets_hosts_and_example_url():
    findings = [_view("h1", "endpoint", "GET /x", {"method": "GET", "kind": "fetch"}, [_occ()])]
    (req,) = build_requests(findings, [_prefix("/x", "https://api.example.com/v3")])
    assert req.path == "/v3/x"
    assert req.hosts == ("api.example.com",)
    assert req.example_url == "https://api.example.com/v3/x"


def test_collision_merges_relative_onto_absolute():
    findings = [
        _view("h1", "endpoint", "GET /address/search", {"method": "GET", "kind": "fetch"},
              [_occ()]),
        _view("h2", "endpoint", "GET /location/address/search", {"method": "GET", "kind": "fetch"},
              [_occ(host="acme.io", raw_url="https://acme.io/location/address/search")]),
        _view("pa", "param", "GET /address/search query:a", {"location": "query", "name": "a"}),
        _view("pb", "param", "GET /location/address/search query:b", {"location": "query", "name": "b"}),
    ]
    reqs = build_requests(findings, [_prefix("/address", "/location")])
    (merged,) = [r for r in reqs if r.path == "/location/address/search"]
    names = {p.name for p in merged.query_params}
    assert {"a", "b"} <= names            # both operations' params survive the merge
    assert set(merged.endpoint_hashes) == {"h1", "h2"}


def test_input_order_is_deterministic():
    findings = [
        _view("h1", "endpoint", "GET /address/search", {"method": "GET", "kind": "fetch"}, [_occ()]),
        _view("h2", "endpoint", "GET /location/address/search", {"method": "GET", "kind": "fetch"},
              [_occ(host="acme.io", raw_url="https://acme.io/location/address/search")]),
    ]
    rules = [_prefix("/address", "/location")]
    a = build_requests(list(findings), rules)
    b = build_requests(list(reversed(findings)), rules)
    assert [r.operation for r in a] == [r.operation for r in b]
    assert a == b


def test_no_rules_is_unchanged_behavior():
    findings = [_view("h1", "endpoint", "GET /a/b", {"method": "GET", "kind": "fetch"}, [_occ()])]
    (req,) = build_requests(findings)  # default rules=()
    assert req.path == "/a/b" and req.operation == "GET /a/b"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/probe/reconstruct_base_url_test.py -v`
Expected: FAIL — `TypeError: build_requests() takes 1 positional argument but 2 were given`.

- [ ] **Step 3: Extend `build_requests` and `reconstruct_run`**

```python
# src/recon/probe/reconstruct.py — extend imports (:14-17)
from dataclasses import dataclass, replace

from recon.findings import base_url, normalize, queries

# add these two private helpers above build_requests
def _apply_rule(request: ReconstructedRequest, rules: list[base_url.BaseUrlRule]) -> ReconstructedRequest:
    """Apply the base-URL overlay to one assembled request (post param-join, gate
    B2). Candidate gate uses request.hosts (empty == host-less)."""
    if not request.probeable:
        return request
    resolved = base_url.resolve_operation(
        request.method, request.path, request.endpoint_hashes, bool(request.hosts), rules
    )
    if not resolved.changed:
        return request
    hosts = request.hosts
    example_url = request.example_url
    if resolved.host:
        hosts = tuple(sorted(set(request.hosts) | {resolved.host}))
        example_url = f"{resolved.scheme}://{resolved.host}{resolved.path}"
    return replace(
        request,
        path=resolved.path,
        operation=f"{request.method} {resolved.path}",
        hosts=hosts,
        example_url=example_url,
    )


def _merge(a: ReconstructedRequest, b: ReconstructedRequest) -> ReconstructedRequest:
    """Order-independent merge of two requests that resolved onto the same
    operation: union query/body params, hosts, endpoint_hashes; deterministic
    example_url."""
    by_name = {p.name: p for p in a.query_params}
    for param in b.query_params:
        by_name.setdefault(param.name, param)
    query_params = tuple(by_name[name] for name in sorted(by_name))
    body_params = tuple(sorted(set(a.body_params) | set(b.body_params)))
    hosts = tuple(sorted(set(a.hosts) | set(b.hosts)))
    endpoint_hashes = tuple(sorted(set(a.endpoint_hashes) | set(b.endpoint_hashes)))
    example_url = min(filter(None, (a.example_url, b.example_url)), default=None)
    return replace(
        a,
        hosts=hosts,
        query_params=query_params,
        body_params=body_params,
        content_type=a.content_type or b.content_type,
        example_url=example_url,
        endpoint_hashes=endpoint_hashes,
    )
```

Then change `build_requests`'s signature and add the post-loop overlay pass:

```python
# change the signature (:53)
def build_requests(
    findings: list[queries.FindingView],
    rules: list[base_url.BaseUrlRule] = (),
) -> list[ReconstructedRequest]:
    # ... the existing body is unchanged, up to and including `return requests`.
    # Replace the final `return requests` with:
    if not rules:
        return requests
    merged: dict[str, ReconstructedRequest] = {}
    for request in (_apply_rule(r, rules) for r in requests):
        if request.operation in merged:
            merged[request.operation] = _merge(merged[request.operation], request)
        else:
            merged[request.operation] = request
    return [merged[operation] for operation in sorted(merged)]
```

```python
# change reconstruct_run (:130) to load and pass the rules
def reconstruct_run(tenant_id: str, run_id: str) -> list[ReconstructedRequest] | None:
    view = queries.list_findings(tenant_id, run_id)
    if view is None:
        return None
    rules = queries.list_base_url_rules(tenant_id, run_id)
    return build_requests(view.findings, rules)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/probe/reconstruct_base_url_test.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Confirm no regressions in the existing reconstruct/export/probe tests**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/probe/reconstruct_test.py src/recon/probe/openapi_test.py src/recon/probe/serialize_test.py -v`
Expected: PASS (the `rules=()` default preserves the existing behavior).

- [ ] **Step 6: Commit**

```bash
git add src/recon/probe/reconstruct.py src/recon/probe/reconstruct_base_url_test.py
git commit -m "feat(req-c2): apply base-URL overlay in reconstruct (post-join, merge)"
```

---

### Task 5: Host-gate + apply the overlay in `_classify_session`

The classify-side application (gate B1): compute the set of endpoint `finding_hash`es that have any occurrence with a host, then apply the resolver only to the host-less ones before `classify_operation`.

**Files:**
- Modify: `src/recon/spec/service.py`
- Test: `src/recon/spec/base_url_classify_test.py`

**Interfaces:**
- Consumes: `queries.base_url_rules_in_session` (Task 3), `base_url.resolve_operation` (Task 2), `normalize.operation_of_endpoint_value` (`normalize.py:238`), `models.FindingOccurrence` (`models.py:258`).
- Produces: none (internal change to `_classify_session`).

- [ ] **Step 1: Write the failing test**

```python
# src/recon/spec/base_url_classify_test.py
import pytest

from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import store
from recon.spec import service

pytestmark = pytest.mark.integration

_SPEC = (
    b'{"openapi":"3.0.3","info":{"title":"t","version":"0"},'
    b'"paths":{"/location/address/search":{"get":{"responses":{"default":{"description":"x"}}}}}}'
)


def _run_with_relative_endpoint(tenant, session_id):
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        # A RELATIVE endpoint (no occurrence host) — its base lives in another file.
        store.record_finding(
            session, tenant_id=tenant, run_id=run_id, finding_type=FindingType.ENDPOINT,
            value="GET /address/search", path="app.js",
            occurrence=store.Occurrence(host=None, raw_url="/address/search"),
            attributes={"method": "GET", "kind": "fetch"}, first_stage="analyzing",
        )
        return run_id


def _status(tenant, session_id, value="GET /address/search"):
    from recon.findings.normalize import finding_hash
    h = finding_hash("endpoint", value, "app.js")
    with tenant_session(tenant) as session:
        row = session.query(models.FindingSpecStatus).filter_by(
            session_id=session_id, finding_hash=h,
        ).one()
        return row.status


def test_set_base_flips_unresolved_to_documented(authorized_session):
    tenant, session_id = authorized_session
    run_id = _run_with_relative_endpoint(tenant, session_id)
    # Attach the spec: /address/search is a suffix of /location/address/search -> unresolved.
    service.attach_and_classify(tenant, run_id, _SPEC)
    assert _status(tenant, session_id) == "unresolved"
    # Add a base rule and reclassify -> documented.
    with tenant_session(tenant) as session:
        session.add(models.SessionBaseUrl(
            tenant_id=tenant, session_id=session_id, kind="prefix",
            path_prefix="/address", base_url="/location",
        ))
    service.reclassify_run(tenant, run_id)
    assert _status(tenant, session_id) == "documented"


def test_absolute_op_stays_documented_under_broad_prefix(authorized_session):
    tenant, session_id = authorized_session
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        # An ABSOLUTE endpoint (occurrence has a host) already documented as /location/....
        store.record_finding(
            session, tenant_id=tenant, run_id=run_id, finding_type=FindingType.ENDPOINT,
            value="GET /location/address/search", path="app.js",
            occurrence=store.Occurrence(host="acme.io",
                                        raw_url="https://acme.io/location/address/search"),
            attributes={"method": "GET", "kind": "fetch"}, first_stage="analyzing",
        )
    service.attach_and_classify(tenant, run_id, _SPEC)
    assert _status(tenant, session_id, "GET /location/address/search") == "documented"
    # A broad prefix that WOULD double-prepend if the host-gate were missing (gate B1).
    with tenant_session(tenant) as session:
        session.add(models.SessionBaseUrl(
            tenant_id=tenant, session_id=session_id, kind="prefix",
            path_prefix="/location", base_url="/x",
        ))
    service.reclassify_run(tenant, run_id)
    assert _status(tenant, session_id, "GET /location/address/search") == "documented"
```

- [ ] **Step 2: Run test to verify it fails**

Run (stack up): `./.venv/Scripts/python.exe -m pytest src/recon/spec/base_url_classify_test.py -v`
Expected: FAIL — the base rule has no effect yet, so `test_set_base_flips...` still sees `unresolved` after reclassify.

- [ ] **Step 3: Host-gate + apply the resolver in `_classify_session`**

```python
# src/recon/spec/service.py — add imports near the top (:36-42 block)
from recon.findings import base_url, normalize, queries

# inside _classify_session, AFTER the `rows = session.execute(...).all()` block (:182-190)
# and BEFORE the `verdicts` loop (:192), insert:

    rules = queries.base_url_rules_in_session(session, session_id)
    host_bearing_hashes = {
        finding_hash
        for (finding_hash,) in session.execute(
            select(models.Finding.finding_hash)
            .distinct()
            .join(models.FindingOccurrence, models.FindingOccurrence.finding_id == models.Finding.id)
            .join(models.Run, models.Run.id == models.Finding.run_id)
            .where(
                models.Run.session_id == session_id,
                models.Finding.type == FindingType.ENDPOINT.value,
                models.FindingOccurrence.host.isnot(None),
            )
        ).all()
    }

# then, INSIDE the `for row_tenant_id, finding_hash, value in rows:` loop, replace
#   classification = classify_operation(value, documented)
# with:

        operation = normalize.operation_of_endpoint_value(value)
        method, _sep, path = operation.partition(" ")
        resolved = base_url.resolve_operation(
            method, path or "/", (finding_hash,), finding_hash in host_bearing_hashes, rules
        )
        classification = classify_operation(f"{method} {resolved.path}", documented)
```

Note: `select` and `models` are already imported in `service.py`; add only the `recon.findings` import.

- [ ] **Step 4: Run test to verify it passes**

Run (stack up): `./.venv/Scripts/python.exe -m pytest src/recon/spec/base_url_classify_test.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Confirm the existing classify/service tests still pass**

Run (stack up): `./.venv/Scripts/python.exe -m pytest src/recon/spec/service_test.py src/recon/spec/classify_test.py -v`
Expected: PASS (no rules attached => `resolve_operation` returns the path unchanged, so verdicts are identical).

- [ ] **Step 6: Commit**

```bash
git add src/recon/spec/service.py src/recon/spec/base_url_classify_test.py
git commit -m "feat(req-c2): host-gated base-URL overlay in _classify_session"
```

---

### Task 6: Rule store + reclassify (`spec/base_url_service.py`)

The write path: validate the base, upsert (prefix) or insert (selection) the rule into the run's session, then reclassify. `None` when the run is invisible (RLS → router 404).

**Files:**
- Create: `src/recon/spec/base_url_service.py`
- Test: `src/recon/spec/base_url_service_test.py`

**Interfaces:**
- Consumes: `base_url.validate_base_url`/`InvalidBaseUrl` (Task 2); `reclassify_run` (`service.py:83`); `queries.base_url_rules_in_session` (Task 3); `pg_insert`, `tenant_session`, `models`.
- Produces: `add_rule(tenant_id, run_id, *, kind, base_url, path_prefix=None, finding_hashes=None, actor=None) -> dict | None`; `list_rules(tenant_id, run_id) -> list[dict] | None`; `delete_rule(tenant_id, run_id, rule_id) -> bool | None`.

- [ ] **Step 1: Write the failing test**

```python
# src/recon/spec/base_url_service_test.py
import pytest

from recon.db import models
from recon.db.base import tenant_session
from recon.findings.base_url import InvalidBaseUrl
from recon.spec import base_url_service

pytestmark = pytest.mark.integration


def _run(tenant, session_id):
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        return str(run.id)


def test_add_list_delete_prefix_rule(authorized_session):
    tenant, session_id = authorized_session
    run_id = _run(tenant, session_id)

    rule = base_url_service.add_rule(
        tenant, run_id, kind="prefix", base_url="/location", path_prefix="/address",
    )
    assert rule["kind"] == "prefix" and rule["base_url"] == "/location"

    rules = base_url_service.list_rules(tenant, run_id)
    assert len(rules) == 1 and rules[0]["path_prefix"] == "/address"

    assert base_url_service.delete_rule(tenant, run_id, rule["id"]) is True
    assert base_url_service.list_rules(tenant, run_id) == []


def test_add_prefix_rule_upserts_on_prefix(authorized_session):
    tenant, session_id = authorized_session
    run_id = _run(tenant, session_id)
    base_url_service.add_rule(tenant, run_id, kind="prefix", base_url="/a", path_prefix="/p")
    base_url_service.add_rule(tenant, run_id, kind="prefix", base_url="/b", path_prefix="/p")
    rules = base_url_service.list_rules(tenant, run_id)
    assert len(rules) == 1 and rules[0]["base_url"] == "/b"  # second overwrote the first


def test_add_rule_invalid_base_raises(authorized_session):
    tenant, session_id = authorized_session
    run_id = _run(tenant, session_id)
    with pytest.raises(InvalidBaseUrl):
        base_url_service.add_rule(tenant, run_id, kind="prefix", base_url="ftp://x", path_prefix="/p")


def test_add_rule_unknown_run_is_none(authorized_session):
    tenant, _session_id = authorized_session
    assert base_url_service.add_rule(
        tenant, "00000000-0000-0000-0000-000000000000",
        kind="prefix", base_url="/a", path_prefix="/p",
    ) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run (stack up): `./.venv/Scripts/python.exe -m pytest src/recon/spec/base_url_service_test.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'recon.spec.base_url_service'`.

- [ ] **Step 3: Write the service**

```python
# src/recon/spec/base_url_service.py
"""REQ-C2 manual base-URL rules — the write path (spec §6).

Validate + persist a rule into the run's session, then reclassify so the shadow
verdicts stay in sync. Read-time consumers (reconstruct/export) reflect a rule
live regardless. `None` when the run is invisible to the tenant (RLS) -> the
router maps that to 404.

Two-transaction note (spec §6, gate N5): the rule is persisted in one
tenant_session, then reclassify_run opens its own. Harmless — reconstruct/export
reflect rules live and reclassify is idempotent and re-runnable.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from recon.db import models
from recon.db.base import tenant_session
from recon.findings import base_url
from recon.findings.queries import base_url_rules_in_session  # noqa: F401  (kept for parity/testing)
from recon.spec.service import reclassify_run


def _as_dict(row: models.SessionBaseUrl) -> dict:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "path_prefix": row.path_prefix,
        "finding_hashes": list(row.finding_hashes or ()),
        "base_url": row.base_url,
        "actor": row.actor,
    }


def add_rule(
    tenant_id: str,
    run_id: str,
    *,
    kind: str,
    base_url: str,  # noqa: A002 — the domain term; shadows the module name only in this frame
    path_prefix: str | None = None,
    finding_hashes: list[str] | None = None,
    actor: str | None = None,
) -> dict | None:
    from recon.findings import base_url as base_url_mod

    base_url_mod.validate_base_url(base_url)  # InvalidBaseUrl -> router 422
    with tenant_session(tenant_id) as session:
        run = session.get(models.Run, run_id)
        if run is None:
            return None
        session_id = str(run.session_id)
        values = dict(
            tenant_id=tenant_id, session_id=session_id, kind=kind,
            path_prefix=path_prefix, finding_hashes=finding_hashes,
            base_url=base_url, actor=actor,
        )
        if kind == "prefix":
            stmt = pg_insert(models.SessionBaseUrl).values(**values).on_conflict_do_update(
                index_elements=["session_id", "path_prefix"],
                set_={"base_url": base_url, "actor": actor, "updated_at": func.now()},
            ).returning(models.SessionBaseUrl)
        else:
            stmt = pg_insert(models.SessionBaseUrl).values(**values).returning(models.SessionBaseUrl)
        row = session.scalars(stmt).one()
        result = _as_dict(row)
    reclassify_run(tenant_id, run_id)  # own transaction (gate N5)
    return result


def list_rules(tenant_id: str, run_id: str) -> list[dict] | None:
    with tenant_session(tenant_id) as session:
        run = session.get(models.Run, run_id)
        if run is None:
            return None
        rows = session.scalars(
            select(models.SessionBaseUrl)
            .where(models.SessionBaseUrl.session_id == str(run.session_id))
            .order_by(models.SessionBaseUrl.created_at)
        ).all()
        return [_as_dict(row) for row in rows]


def delete_rule(tenant_id: str, run_id: str, rule_id: str) -> bool | None:
    try:
        rule_uuid = uuid.UUID(rule_id)
    except ValueError:
        return False
    with tenant_session(tenant_id) as session:
        run = session.get(models.Run, run_id)
        if run is None:
            return None
        result = session.execute(
            delete(models.SessionBaseUrl).where(
                models.SessionBaseUrl.id == rule_uuid,
                models.SessionBaseUrl.session_id == str(run.session_id),
            )
        )
        deleted = result.rowcount > 0
    if deleted:
        reclassify_run(tenant_id, run_id)
    return deleted
```

- [ ] **Step 4: Run test to verify it passes**

Run (stack up): `./.venv/Scripts/python.exe -m pytest src/recon/spec/base_url_service_test.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/recon/spec/base_url_service.py src/recon/spec/base_url_service_test.py
git commit -m "feat(req-c2): base-URL rule store + reclassify trigger"
```

---

### Task 7: Route + app wiring (`api/base_url_router.py`)

The thin REST surface: `POST/GET/DELETE /runs/{run_id}/base-url`. Mirrors `spec_router` (tenant dep, `run_in_threadpool`, `None`→404); an invalid base or a kind/field mismatch → 422.

**Files:**
- Create: `src/recon/api/base_url_router.py`
- Modify: `src/recon/api/app.py`
- Test: `src/recon/api/base_url_router_test.py`

**Interfaces:**
- Consumes: `base_url_service` (Task 6), `get_tenant_id` (`deps.py:24`), `InvalidBaseUrl` (Task 2).
- Produces: `router` (an `APIRouter`) with the three routes.

- [ ] **Step 1: Write the failing test**

```python
# src/recon/api/base_url_router_test.py
import pytest
from fastapi.testclient import TestClient

from recon.api.app import create_app
from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import store
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration

_SPEC = (
    b'{"openapi":"3.0.3","info":{"title":"t","version":"0"},'
    b'"paths":{"/location/address/search":{"get":{"responses":{"default":{"description":"x"}}}}}}'
)


@pytest.fixture()
def client():
    return TestClient(create_app())


def _headers(tenant):
    return {"X-Tenant-Id": tenant}


def _seed_relative(tenant, session_id):
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        store.record_finding(
            session, tenant_id=tenant, run_id=run_id, finding_type=FindingType.ENDPOINT,
            value="GET /address/search", path="app.js",
            occurrence=store.Occurrence(host=None, raw_url="/address/search"),
            attributes={"method": "GET", "kind": "fetch"}, first_stage="analyzing",
        )
        return run_id


def test_post_prefix_rule_documents_the_endpoint(client, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed_relative(tenant, session_id)
    client.post(f"/runs/{run_id}/spec", headers=_headers(tenant), content=_SPEC)

    resp = client.post(
        f"/runs/{run_id}/base-url", headers=_headers(tenant),
        json={"kind": "prefix", "path_prefix": "/address", "base_url": "/location"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["rule"]["base_url"] == "/location"
    assert body["summary"]["documented"] == 1  # unresolved -> documented after the rule


def test_get_lists_rules(client, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed_relative(tenant, session_id)
    client.post(f"/runs/{run_id}/base-url", headers=_headers(tenant),
                json={"kind": "prefix", "path_prefix": "/address", "base_url": "/location"})
    resp = client.get(f"/runs/{run_id}/base-url", headers=_headers(tenant))
    assert resp.status_code == 200 and len(resp.json()) == 1


def test_delete_rule(client, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed_relative(tenant, session_id)
    rule = client.post(f"/runs/{run_id}/base-url", headers=_headers(tenant),
                       json={"kind": "prefix", "path_prefix": "/a", "base_url": "/b"}).json()["rule"]
    resp = client.delete(f"/runs/{run_id}/base-url/{rule['id']}", headers=_headers(tenant))
    assert resp.status_code == 204
    assert client.get(f"/runs/{run_id}/base-url", headers=_headers(tenant)).json() == []


def test_invalid_base_is_422(client, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed_relative(tenant, session_id)
    resp = client.post(f"/runs/{run_id}/base-url", headers=_headers(tenant),
                       json={"kind": "prefix", "path_prefix": "/a", "base_url": "ftp://x"})
    assert resp.status_code == 422


def test_kind_field_mismatch_is_422(client, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed_relative(tenant, session_id)
    resp = client.post(f"/runs/{run_id}/base-url", headers=_headers(tenant),
                       json={"kind": "prefix", "finding_hashes": ["h"], "base_url": "/a"})
    assert resp.status_code == 422  # prefix requires path_prefix, not finding_hashes


def test_unknown_run_is_404(client, tenant):
    resp = client.post("/runs/00000000-0000-0000-0000-000000000000/base-url",
                       headers=_headers(tenant),
                       json={"kind": "prefix", "path_prefix": "/a", "base_url": "/b"})
    assert resp.status_code == 404


def test_other_tenant_run_is_404(client, authorized_session):
    owner_tenant, session_id = authorized_session
    run_id = _seed_relative(owner_tenant, session_id)
    other = sessions_service.create_tenant("base-url-other")
    resp = client.post(f"/runs/{run_id}/base-url", headers=_headers(other),
                       json={"kind": "prefix", "path_prefix": "/a", "base_url": "/b"})
    assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run (stack up): `./.venv/Scripts/python.exe -m pytest src/recon/api/base_url_router_test.py -v`
Expected: FAIL — the route does not exist (the SPA fallback 404s the POST, and `base_url_router` is not importable in the next step yet).

- [ ] **Step 3: Write the router**

```python
# src/recon/api/base_url_router.py
"""Manual base-URL rules for a run's session (REQ-C2, spec §6).

POST/GET/DELETE /runs/{run_id}/base-url. Thin: validate the body, delegate to
recon.spec.base_url_service (which persists + reclassifies), map RLS-invisible
runs to 404 and an invalid base/kind to 422. Isolation is the database's (RLS).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from recon.api.deps import get_tenant_id
from recon.findings.base_url import InvalidBaseUrl
from recon.spec import base_url_service

router = APIRouter(tags=["base-url"])


class BaseUrlRuleIn(BaseModel):
    kind: Literal["prefix", "selection"]
    base_url: str
    path_prefix: str | None = None
    finding_hashes: list[str] | None = None
    actor: str | None = None


def _validate_shape(rule: BaseUrlRuleIn) -> None:
    if rule.kind == "prefix" and (not rule.path_prefix or rule.finding_hashes):
        raise HTTPException(status_code=422, detail="a prefix rule needs path_prefix and no finding_hashes")
    if rule.kind == "selection" and (not rule.finding_hashes or rule.path_prefix):
        raise HTTPException(status_code=422, detail="a selection rule needs finding_hashes and no path_prefix")


@router.post("/runs/{run_id}/base-url")
async def add_base_url_rule(
    run_id: str, rule: BaseUrlRuleIn, tenant_id: str = Depends(get_tenant_id),
) -> dict:
    _validate_shape(rule)
    try:
        created = await run_in_threadpool(
            base_url_service.add_rule, tenant_id, run_id,
            kind=rule.kind, base_url=rule.base_url, path_prefix=rule.path_prefix,
            finding_hashes=rule.finding_hashes, actor=rule.actor,
        )
    except InvalidBaseUrl as exc:
        raise HTTPException(status_code=422, detail=f"invalid base_url: {exc}") from exc
    if created is None:
        raise HTTPException(status_code=404, detail="run not found")
    summary = await run_in_threadpool(base_url_service.list_rules, tenant_id, run_id)  # noqa: F841
    from dataclasses import asdict  # local import to avoid an unused import if refactored

    from recon.spec.service import reclassify_run  # noqa: F401

    # The service already reclassified; re-derive the run-scoped summary for the client.
    from recon.spec import service as spec_service

    run_summary = await run_in_threadpool(spec_service.reclassify_run, tenant_id, run_id)
    return {"rule": created, "summary": asdict(run_summary) if run_summary else None}


@router.get("/runs/{run_id}/base-url")
async def list_base_url_rules(
    run_id: str, tenant_id: str = Depends(get_tenant_id),
) -> list[dict]:
    rules = await run_in_threadpool(base_url_service.list_rules, tenant_id, run_id)
    if rules is None:
        raise HTTPException(status_code=404, detail="run not found")
    return rules


@router.delete("/runs/{run_id}/base-url/{rule_id}", status_code=204)
async def delete_base_url_rule(
    run_id: str, rule_id: str, tenant_id: str = Depends(get_tenant_id),
) -> Response:
    deleted = await run_in_threadpool(base_url_service.delete_rule, tenant_id, run_id, rule_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail="run not found")
    if not deleted:
        raise HTTPException(status_code=404, detail="rule not found")
    return Response(status_code=204)
```

Note: the POST re-runs `reclassify_run` once to obtain the run-scoped `SpecSummary` for the response (it is idempotent). If you prefer a single reclassify, have `base_url_service.add_rule` return `(rule_dict, summary)` and thread it out — but keep the extra call unless a test shows it matters; simplicity over a micro-optimization here.

- [ ] **Step 4: Wire the router into the app**

```python
# src/recon/api/app.py — add to the import block (:16-23)
from recon.api import (
    base_url_router,
    export_router,
    findings_router,
    probe_router,
    runs_router,
    sessions_router,
    spec_router,
)

# register it in create_app(), before _mount_spa (after :41)
    app.include_router(export_router.router)
    app.include_router(base_url_router.router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run (stack up): `./.venv/Scripts/python.exe -m pytest src/recon/api/base_url_router_test.py -v`
Expected: PASS (7 tests).

- [ ] **Step 6: Full host lane (no regressions)**

Run: `./.venv/Scripts/python.exe -m pytest -m "not integration"`
Expected: exit 0 (306 baseline + the new pure tests from Tasks 1, 2, 4).

- [ ] **Step 7: Commit**

```bash
git add src/recon/api/base_url_router.py src/recon/api/app.py src/recon/api/base_url_router_test.py
git commit -m "feat(req-c2): POST/GET/DELETE /runs/{id}/base-url route"
```

---

### Task 8: Frontend panel (`web/src/features/findings/BaseUrlPanel.tsx`)

The analyst control: add a prefix or selection rule, list rules, delete one. Mirrors `SpecUpload.tsx` (a `card` form using `useTenant` + the api client). After a change it shows the returned bucket summary so the re-resolution's effect is visible.

**Files:**
- Modify: `web/src/api/types.ts`
- Modify: `web/src/api/apiClient.ts`
- Create: `web/src/features/findings/BaseUrlPanel.tsx`
- Test: `web/src/features/findings/BaseUrlPanel.test.tsx`

**Interfaces:**
- Consumes: `useTenant` (`tenant/TenantContext`), `ApiError`, the API functions below.
- Produces: `BaseUrlRule` type; `listBaseUrlRules` / `addBaseUrlRule` / `deleteBaseUrlRule`; `BaseUrlPanel`.

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/features/findings/BaseUrlPanel.test.tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BaseUrlPanel } from "./BaseUrlPanel";
import { TenantProvider } from "../../tenant/TenantContext";
import * as api from "../../api/apiClient";

const TENANT = "123e4567-e89b-12d3-a456-426614174000";
beforeEach(() => { vi.restoreAllMocks(); localStorage.setItem("recon.tenantId", TENANT); });

function ui() {
  vi.spyOn(api, "listBaseUrlRules").mockResolvedValue([]);
  return render(<TenantProvider><BaseUrlPanel runId="r" /></TenantProvider>);
}

describe("BaseUrlPanel", () => {
  it("posts a prefix rule and lists it", async () => {
    vi.spyOn(api, "addBaseUrlRule").mockResolvedValue({
      rule: { id: "1", kind: "prefix", path_prefix: "/address", finding_hashes: [], base_url: "/location", actor: null },
      summary: { documented: 1, shadow: 0, unresolved: 0, suffix_verify: 0, base_url_incompleteness_ratio: 0 },
    });
    ui();
    await userEvent.type(screen.getByLabelText(/path prefix/i), "/address");
    await userEvent.type(screen.getByLabelText(/base url/i), "/location");
    await userEvent.click(screen.getByRole("button", { name: /add rule/i }));
    expect(api.addBaseUrlRule).toHaveBeenCalledWith(TENANT, "r", {
      kind: "prefix", path_prefix: "/address", base_url: "/location",
    });
    expect(await screen.findByText(/documented 1/)).toBeInTheDocument();
  });

  it("shows a readable message on a 422 invalid base", async () => {
    vi.spyOn(api, "addBaseUrlRule").mockRejectedValue(new api.ApiError(422, "invalid base_url: ..."));
    ui();
    await userEvent.type(screen.getByLabelText(/path prefix/i), "/a");
    await userEvent.type(screen.getByLabelText(/base url/i), "ftp://x");
    await userEvent.click(screen.getByRole("button", { name: /add rule/i }));
    expect(await screen.findByText(/invalid base_url/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npm run test -- BaseUrlPanel`
Expected: FAIL — cannot resolve `./BaseUrlPanel` / `addBaseUrlRule` is not exported.

- [ ] **Step 3: Add the type + api-client functions**

```ts
// web/src/api/types.ts — add
export interface BaseUrlRule {
  id: string;
  kind: "prefix" | "selection";
  path_prefix: string | null;
  finding_hashes: string[];
  base_url: string;
  actor: string | null;
}
export interface BaseUrlRuleResult { rule: BaseUrlRule; summary: SpecSummary | null; }
```

```ts
// web/src/api/apiClient.ts — add BaseUrlRule/BaseUrlRuleResult to the type import on line 1, then:
export function listBaseUrlRules(tenantId: string, runId: string): Promise<BaseUrlRule[]> {
  return request(`/runs/${encodeURIComponent(runId)}/base-url`, {}, tenantId);
}

export function addBaseUrlRule(
  tenantId: string, runId: string,
  body: { kind: "prefix" | "selection"; base_url: string; path_prefix?: string; finding_hashes?: string[] },
): Promise<BaseUrlRuleResult> {
  return request(`/runs/${encodeURIComponent(runId)}/base-url`, json("POST", body), tenantId);
}

export function deleteBaseUrlRule(tenantId: string, runId: string, ruleId: string): Promise<void> {
  return request(
    `/runs/${encodeURIComponent(runId)}/base-url/${encodeURIComponent(ruleId)}`,
    { method: "DELETE" }, tenantId,
  );
}
```

- [ ] **Step 4: Write the component**

```tsx
// web/src/features/findings/BaseUrlPanel.tsx
import { useEffect, useState } from "react";
import type React from "react";
import { useTenant } from "../../tenant/TenantContext";
import { addBaseUrlRule, deleteBaseUrlRule, listBaseUrlRules, ApiError } from "../../api/apiClient";
import type { BaseUrlRule, SpecSummary } from "../../api/types";

export function BaseUrlPanel({ runId }: { runId: string }) {
  const { tenantId } = useTenant();
  const [rules, setRules] = useState<BaseUrlRule[]>([]);
  const [prefix, setPrefix] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [summary, setSummary] = useState<SpecSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!tenantId) return;
    listBaseUrlRules(tenantId, runId).then(setRules).catch(() => { /* first load best-effort */ });
  }, [tenantId, runId]);

  const ready = prefix.trim() !== "" && baseUrl.trim() !== "";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!ready || !tenantId || busy) return;
    setBusy(true); setError(null);
    try {
      const res = await addBaseUrlRule(tenantId, runId, {
        kind: "prefix", path_prefix: prefix, base_url: baseUrl,
      });
      setRules((prev) => [...prev.filter((r) => r.path_prefix !== res.rule.path_prefix), res.rule]);
      setSummary(res.summary);
      setPrefix(""); setBaseUrl("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to add rule");
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    if (!tenantId) return;
    await deleteBaseUrlRule(tenantId, runId, id);
    setRules((prev) => prev.filter((r) => r.id !== id));
  }

  return (
    <form className="card" onSubmit={submit}>
      <h3>Base URL</h3>
      <p className="muted">Prepend a base to relative endpoints whose path is missing it (cross-file base URL).</p>
      <div>
        <label htmlFor="base-prefix">Path prefix</label>
        <input id="base-prefix" value={prefix} onChange={(e) => setPrefix(e.target.value)}
          placeholder="/address" />
      </div>
      <div>
        <label htmlFor="base-url">Base URL</label>
        <input id="base-url" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)}
          placeholder="/location or https://api.example.com/v3" />
      </div>
      {error && <p className="sev-high">{error}</p>}
      <button type="submit" disabled={!ready || busy}>{busy ? "Adding…" : "Add rule"}</button>
      {summary && (
        <p className="muted">documented {summary.documented} · shadow {summary.shadow} · unresolved {summary.unresolved}</p>
      )}
      <ul>
        {rules.map((r) => (
          <li key={r.id}>
            <code>{r.path_prefix}</code> → <code>{r.base_url}</code>
            <button type="button" onClick={() => remove(r.id)} aria-label={`Delete rule ${r.path_prefix}`}>Delete</button>
          </li>
        ))}
      </ul>
    </form>
  );
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd web && npm run test -- BaseUrlPanel`
Expected: PASS (2 tests).

- [ ] **Step 6: Render the panel next to `SpecUpload`, and surface `matched_operation` (gate N4)**

In the findings view where `SpecUpload` is rendered (search `web/src/features/findings/FindingsView.tsx` for `<SpecUpload`), render `<BaseUrlPanel runId={runId} />` beside it. Then, where a finding's `spec_status` is displayed (`FindingDetail.tsx`), also render `spec_status.matched_operation` when present — so an analyst sees the resolved documented op (`GET /location/address/search`) next to the unchanged raw value (`/address/search`), which is expected under identity non-churn. Mirror the existing `spec_status` rendering; do not invent new styling.

- [ ] **Step 7: Run the FE suite (no regressions)**

Run: `cd web && npm run test`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add web/src/api/types.ts web/src/api/apiClient.ts web/src/features/findings/BaseUrlPanel.tsx web/src/features/findings/BaseUrlPanel.test.tsx web/src/features/findings/FindingsView.tsx web/src/features/findings/FindingDetail.tsx
git commit -m "feat(req-c2): base-URL rule panel + matched_operation display"
```

---

## Self-review (checked against the spec)

- **Spec coverage:** §2 settled decisions → Global Constraints; §3 files → Tasks 1-8 file map; §4 data model + RLS (B3) → Task 1; §5 resolver semantics (candidate gate B1, post-join B2, precedence, prepend/segment-boundary/idempotence, host→example_url N2, collision-merge/determinism N3) → Tasks 2 & 4 & 5; §6 route + reclassify + two-transaction N5 → Tasks 6-7; §7 errors (404/422) → Tasks 6-7; §8 tests (pure resolver, integration flip + B1, FE + matched_operation N4) → Tasks 2/4/5/7/8. No gaps.
- **Placeholder scan:** every code/test step contains real code and real commands; no TBD/TODO. The one prose step (Task 8 Step 6) is a display mirror against named files, not a code placeholder.
- **Type consistency:** `BaseUrlRule(kind, base_url, path_prefix, finding_hashes)` and `resolve_operation(method, path, endpoint_hashes, has_host, rules) -> ResolvedOp(path, host, scheme, changed)` are used identically in Tasks 2/4/5; `build_requests(findings, rules=())` extends the real `reconstruct.py:53` signature; `base_url_service.add_rule/list_rules/delete_rule` return shapes match the router and FE `BaseUrlRule`/`BaseUrlRuleResult` types; the RLS SQL and `BASE_URL_TABLES` match `0006`/`models.py` conventions.

## Execution handoff — offered after you approve the plan.
