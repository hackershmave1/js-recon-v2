# Tech Detection Implementation Plan

> **Status: SHIPPED** — kept as the point-in-time implementation plan (a record, not open work). Tech detection is live; `run_technology` shipped as **migration 0017**, not the `0016` predicted throughout below (`0016` became `finding_type_generic` first). The open `- [ ]` checkboxes are historical.

**Goal:** Give every recon run a per-host technology stack (server, framework, CDN, JS libraries, analytics) with versions where detectable, built only from signal the platform already collects, surfaced in the Recon Workspace and structured to feed the future threat model.

**Architecture:** All network I/O stays in the fetch/capture stages, which harvest an allowlisted, secret-free "fingerprint-signal" blob per run (headers + script URLs + `<meta generator>` + cookie NAMES, keyed by host from observed asset URLs). A pure-Python matcher (`recon.findings.techdetect`) runs a vendored enthec/webappanalyzer fingerprint dataset over that signal with `google-re2` (linear-time, ReDoS-safe) during ANALYZE, best-effort, and upserts one `run_technology` table. A thin API + a web Tech page read it back.

**Tech Stack:** Python 3.11 · FastAPI · SQLAlchemy 2 + Postgres (RLS) · Alembic · Redis Streams · S3/MinIO blobs · `google-re2` · React/Vite + TypeScript · vitest.

## Global Constraints
- File cap ~300 lines; split when exceeded — `techdetect` is a **package** of <300-line modules (T9).
- mypy `--strict` runs on `recon.findings.*` (and `recon.spec.*`) — so **all of `recon.findings.techdetect.*`, `recon.findings.analyze`, and `recon.findings.queries` must be fully typed** (typed `re2` adapter + `TypedDict`/`cast` for the vendored JSON — T8). The ORM model lives in `recon.db.models` (NOT strict).
- ruff selects `F,I,UP,B,C4,SIM,PIE,RET` + `ruff format --check`.
- Host lane: `RECON_REQUIRE_ENGINES=1 uv run pytest -m "not integration" --cov=recon --cov-fail-under=60` (coverage floor **60**).
- Colocated `*_test.py` next to source (backend); `*.test.tsx` (web).
- Commit style: Conventional Commits, multi-line, ending with the trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- `google-re2` pinned with win + linux wheels (latest `1.1.20251105`); per-pattern defensive compile so a lookbehind reject is skipped, not fatal (T4).
- enthec/webappanalyzer dataset is **GPL-3.0** — server-side only (SaaS/internal), never bundled into the extension; a CI guard enforces it (T10).
- **Migration numbering — CORRECTION vs. the design doc.** The design (2026-08-16) said "head is 0014, new is 0015", but the branch has since gained `0015_finding_type_unresolved` (committed at `05c56fa`). The current head is therefore `0015_finding_type_unresolved`, so **the new migration is `0016_technology.py` with `down_revision = "0015_finding_type_unresolved"`** (using `0015` again would create two alembic heads). Everything else in the design stands.

---

### Task 1: `RunTechnology` model + `TECH_TABLES` + migration 0016 + `BLOB_KINDS`
**Files:**
- Modify `apps/platform/src/recon/storage.py` (`BLOB_KINDS` frozenset, lines 24-38)
- Modify `apps/platform/src/recon/db/models.py` (add `RunTechnology` class after `RunAsset` ~line 415; add `TECH_TABLES` after the RLS groups, ~line 608)
- Create `apps/platform/src/recon/migrations/versions/0016_technology.py`
- Test: `apps/platform/src/recon/db/run_technology_model_test.py`

**Interfaces:** Produces ORM `models.RunTechnology` (columns `id, tenant_id, run_id, host, name, categories, version, confidence, evidence, created_at`; `UNIQUE(run_id, host, name)` named `uq_run_technology_run_host_name`; `INDEX(tenant_id, run_id)`), `models.TECH_TABLES = ("run_technology",)`, and the string `"fingerprint-signal"` in `storage.BLOB_KINDS`.

- [ ] **Step 1: Write the failing test** — `apps/platform/src/recon/db/run_technology_model_test.py`:
```python
import pytest

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


def _make_run(tenant: str, session_id: str) -> str:
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id)
        session.add(run)
        session.flush()
        return str(run.id)


def test_fingerprint_signal_is_a_known_blob_kind():
    assert "fingerprint-signal" in storage.BLOB_KINDS


def test_run_technology_is_tenant_isolated_by_rls():
    tenant_a = sessions_service.create_tenant("tech-a")
    tenant_b = sessions_service.create_tenant("tech-b")
    sv = sessions_service.create_session(
        tenant_a, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    run_id = _make_run(tenant_a, sv.id)
    with tenant_session(tenant_a) as session:
        session.add(
            models.RunTechnology(
                tenant_id=tenant_a,
                run_id=run_id,
                host="acme.io",
                name="nginx",
                categories=["Web servers"],
                version="1.25.3",
                confidence=100,
                evidence=["server: nginx/1.25.3"],
            )
        )
    with tenant_session(tenant_a) as session:
        row = session.query(models.RunTechnology).one()
        assert row.name == "nginx" and row.version == "1.25.3" and row.confidence == 100
    with tenant_session(tenant_b) as session:
        assert session.query(models.RunTechnology).count() == 0


def test_run_technology_unique_on_run_host_name():
    tenant = sessions_service.create_tenant("tech-uq")
    sv = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    run_id = _make_run(tenant, sv.id)
    with tenant_session(tenant) as session:
        for _ in range(2):
            session.add(
                models.RunTechnology(
                    tenant_id=tenant, run_id=run_id, host="acme.io", name="nginx",
                    categories=[], version=None, confidence=50, evidence=[],
                )
            )
        with pytest.raises(Exception):  # IntegrityError on the (run_id, host, name) unique
            session.flush()
```
- [ ] **Step 2: Run test to verify it fails** — bring up infra then run:
  `docker compose up -d postgres redis minio migrate` (from `apps/platform`), then
  `RECON_S3_ENDPOINT_URL=http://localhost:9000 RECON_S3_ACCESS_KEY=recon RECON_S3_SECRET_KEY=recon-secret RECON_S3_BUCKET=recon-artifacts uv run pytest src/recon/db/run_technology_model_test.py -v`
  Expected: `test_fingerprint_signal_is_a_known_blob_kind` fails (`"fingerprint-signal" not in BLOB_KINDS`); the two RLS/unique tests fail with `AttributeError: module 'recon.db.models' has no attribute 'RunTechnology'`.
- [ ] **Step 3: Write minimal implementation.**
  In `storage.py`, add the kind to `BLOB_KINDS` (after `"graphql"`):
```python
        "graphql",
        # Per-run tech-detection signal (allowlisted headers + script URLs + meta
        # markers + cookie NAMES, keyed by host). Never any secret or raw HTML.
        "fingerprint-signal",
```
  In `db/models.py`, add the model after `class RunAsset` (before `class FindingTriage`):
```python
class RunTechnology(Base):
    """Detected technology stack for one host of a run (tech-detection slice).

    Per-run snapshot (CASCADE like ``Finding``), upserted on ``(run_id, host, name)``
    by the best-effort analyze fingerprint pass. ``categories`` and ``evidence`` are
    JSONB lists of strings; ``evidence`` holds only allowlisted, bounded markers —
    never a secret or raw HTML (T1). ``host`` comes from observed asset URLs (T11)."""

    __tablename__ = "run_technology"
    __table_args__ = (
        UniqueConstraint("run_id", "host", "name", name="uq_run_technology_run_host_name"),
        Index("ix_run_technology_run", "tenant_id", "run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("run.id", ondelete="CASCADE"), nullable=False
    )
    host: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    categories: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    version: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    evidence: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    created_at: Mapped[dt.datetime] = _now_col(nullable=False)
```
  Add the RLS group at the end of `db/models.py` (after `ENGAGEMENT_TABLES`):
```python
# Tech-detection addition, RLS-enabled by migration 0016.
TECH_TABLES: tuple[str, ...] = ("run_technology",)
```
  Create `migrations/versions/0016_technology.py` (mirrors `0005_run_asset.py`'s create_all + FORCE RLS):
```python
"""tech-detection run_technology table + RLS

Revision ID: 0016_technology
Revises: 0015_finding_type_unresolved
Create Date: 2026-08-16

The run_technology TABLE is built from live metadata (create_all is idempotent —
only what's missing) then given FORCE RLS + the tenant_isolation policy + GRANT,
exactly like 0005. On a fresh DB / CI, 0001's create_all already made the table
(the model now carries it), so create_all here is a no-op; on an older dev DB it
adds it. No incremental column adds, so no ADD COLUMN IF NOT EXISTS is needed.
"""

from __future__ import annotations

from alembic import op

from recon.db import models
from recon.db.base import Base

revision = "0016_technology"
down_revision = "0015_finding_type_unresolved"
branch_labels = None
depends_on = None

APP_ROLE = "recon_app"


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind)  # idempotent: builds run_technology (+ any missing)
    for table in models.TECH_TABLES:
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
    for table in models.TECH_TABLES:
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"')
    op.drop_table("run_technology")
```
  Re-run the migration so the new table lands in the running DB: `docker compose run --rm migrate` (or `uv run alembic upgrade head` against the compose DB).
- [ ] **Step 4: Run test to verify it passes** —
  `RECON_S3_ENDPOINT_URL=http://localhost:9000 RECON_S3_ACCESS_KEY=recon RECON_S3_SECRET_KEY=recon-secret RECON_S3_BUCKET=recon-artifacts uv run pytest src/recon/db/run_technology_model_test.py -v`
  Expected: 3 passed. Also run `uv run ruff check src/recon/db/models.py src/recon/storage.py` — clean.
- [ ] **Step 5: Commit** —
  `git add apps/platform/src/recon/storage.py apps/platform/src/recon/db/models.py apps/platform/src/recon/migrations/versions/0016_technology.py apps/platform/src/recon/db/run_technology_model_test.py`
```
git commit -m "feat(db): add run_technology table + TECH_TABLES RLS + fingerprint-signal blob kind

Per-host tech-detection results table (per-run snapshot, CASCADE, UNIQUE on
(run_id, host, name)) with FORCE RLS in migration 0016 (down_revision
0015_finding_type_unresolved). Adds the 'fingerprint-signal' blob kind for the
allowlisted per-run signal the fetch/capture stages will harvest.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `techdetect/version.py` — enthec tag mini-parser
**Files:**
- Create `apps/platform/src/recon/findings/techdetect/__init__.py` (empty for now — real body in Task 5)
- Create `apps/platform/src/recon/findings/techdetect/version.py`
- Test: `apps/platform/src/recon/findings/techdetect/version_test.py`

**Interfaces:** Produces `version.PatternTags(version: str | None, confidence: int)`, `version.parse_field_value(raw: str) -> tuple[str, PatternTags]` (splits an enthec field string on the literal `\;` into regex-source + tags), and `version.resolve_version(template: str | None, groups: tuple[str | None, ...]) -> str | None` (substitutes `\1..\9`, handles the ternary `\1?a:b`). These are pure and consumed by `compile.py` (Task 3) and `match.py` (Task 5).

- [ ] **Step 1: Write the failing test** — `apps/platform/src/recon/findings/techdetect/version_test.py`:
```python
from recon.findings.techdetect import version


def test_parse_plain_pattern_has_no_tags():
    regex, tags = version.parse_field_value("Express")
    assert regex == "Express"
    assert tags.version is None
    assert tags.confidence == 100  # enthec default when unspecified


def test_parse_splits_version_and_confidence_tags():
    regex, tags = version.parse_field_value(r"nginx(?:/([\d.]+))?\;version:\1\;confidence:50")
    assert regex == r"nginx(?:/([\d.]+))?"
    assert tags.version == r"\1"
    assert tags.confidence == 50


def test_resolve_substitutes_capture_group():
    assert version.resolve_version(r"\1", ("1.25.3",)) == "1.25.3"


def test_resolve_empty_group_yields_none_not_blank():
    assert version.resolve_version(r"\1", (None,)) is None


def test_resolve_ternary_present_and_absent():
    # \1?a:b -> a when group 1 matched (truthy), b when it didn't
    assert version.resolve_version(r"\1?4:3", ("something",)) == "4"
    assert version.resolve_version(r"\1?4:3", (None,)) == "3"


def test_resolve_none_template_is_none():
    assert version.resolve_version(None, ("x",)) is None
```
- [ ] **Step 2: Run test to verify it fails** — from `apps/platform`:
  `RECON_REQUIRE_ENGINES=1 uv run pytest -m "not integration" src/recon/findings/techdetect/version_test.py -v`
  Expected: `ModuleNotFoundError: No module named 'recon.findings.techdetect'`.
- [ ] **Step 3: Write minimal implementation** — create `techdetect/__init__.py` (empty; filled in Task 5) and `techdetect/version.py`:
```python
"""Parse the enthec/webappanalyzer field-value grammar (Wappalyzer-compatible).

A dataset field value is ``<regex>[\\;version:<template>][\\;confidence:<n>]`` where
``\\;`` is a LITERAL two-char separator (not a regex escape). The version template
substitutes capture groups (``\\1``..``\\9``) and supports one ternary
``\\1?present:absent``. Only OUR tiny, trusted grammar is parsed with stdlib ``re``;
the untrusted dataset regex itself is compiled with ``google-re2`` (see compile.py).
"""

from __future__ import annotations

import re as _stdlib_re
from dataclasses import dataclass

# \1?a:b — a ternary keyed on whether capture group N matched. Non-greedy present
# branch up to the first ':'; absent branch is the remainder.
_TERNARY = _stdlib_re.compile(r"\\(\d)\?([^:]+):(.*)$")
# A bare \N group reference.
_GROUP = _stdlib_re.compile(r"\\(\d)")

_DEFAULT_CONFIDENCE = 100  # enthec default when a field carries no confidence tag


@dataclass(frozen=True)
class PatternTags:
    version: str | None
    confidence: int


def parse_field_value(raw: str) -> tuple[str, PatternTags]:
    """Split an enthec field value into its regex source and its tags."""
    parts = raw.split("\\;")
    regex = parts[0]
    version: str | None = None
    confidence = _DEFAULT_CONFIDENCE
    for tag in parts[1:]:
        key, _, value = tag.partition(":")
        if key == "version":
            version = value
        elif key == "confidence":
            try:
                confidence = int(value)
            except ValueError:
                confidence = _DEFAULT_CONFIDENCE
    return regex, PatternTags(version=version, confidence=confidence)


def resolve_version(template: str | None, groups: tuple[str | None, ...]) -> str | None:
    """Resolve a version template against a match's capture groups, or ``None``.

    ``\\1``..``\\9`` substitute the group (1-indexed; ``\\1`` -> ``groups[0]``); an
    absent group substitutes ``""``. A single ternary ``\\N?a:b`` chooses ``a`` when
    group N matched, else ``b``. An empty result becomes ``None`` (never store "")."""
    if not template:
        return None
    resolved = template
    ternary = _TERNARY.search(resolved)
    if ternary is not None:
        index = int(ternary.group(1))
        chosen = ternary.group(2) if _group(groups, index) else ternary.group(3)
        resolved = resolved.replace(ternary.group(0), chosen)
    for digit in _GROUP.findall(resolved):
        resolved = resolved.replace(f"\\{digit}", _group(groups, int(digit)) or "")
    resolved = resolved.strip()
    return resolved or None


def _group(groups: tuple[str | None, ...], index: int) -> str | None:
    """The 1-indexed capture group, or ``None`` if out of range / unmatched."""
    return groups[index - 1] if 1 <= index <= len(groups) else None
```
- [ ] **Step 4: Run test to verify it passes** — from `apps/platform`:
  `RECON_REQUIRE_ENGINES=1 uv run pytest -m "not integration" src/recon/findings/techdetect/version_test.py -v`
  Expected: 6 passed. Also `uv run mypy src/recon/findings/techdetect/version.py` — no errors (strict-clean).
- [ ] **Step 5: Commit** —
  `git add apps/platform/src/recon/findings/techdetect/__init__.py apps/platform/src/recon/findings/techdetect/version.py apps/platform/src/recon/findings/techdetect/version_test.py`
```
git commit -m "feat(techdetect): add enthec version/confidence tag mini-parser

parse_field_value splits a dataset field on the literal \\; separator into regex
source + tags; resolve_version substitutes \\1..\\9 and the \\1?a:b ternary using
only stdlib re on our own trusted grammar (the untrusted dataset regex compiles
under google-re2). Pure, mypy-strict.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `techdetect/compile.py` — typed google-re2 adapter + defensive compile
**Files:**
- Modify `apps/platform/pyproject.toml` (add `google-re2` to `[project].dependencies`, ~line 59) and run `uv lock`
- Create `apps/platform/src/recon/findings/techdetect/compile.py`
- Test: `apps/platform/src/recon/findings/techdetect/compile_test.py`

**Interfaces:** Consumes `version.parse_field_value`, `version.PatternTags`. Produces the typed adapter `compile.Re2Pattern` (Protocol: `search(text: str) -> Re2Match | None`), `compile.Re2Match` (Protocol: `group(n: int = 0) -> str`, `groups() -> tuple[str | None, ...]`), `compile.compile_pattern(source: str, *, case_insensitive: bool = True) -> Re2Pattern`, and `compile.try_compile(source: str) -> Re2Pattern | None` (returns `None` on a `re2.error` reject). Also `compile.CompiledPattern` and `compile.CompiledTech` dataclasses + `compile.compile_all(raw_techs: dict[str, dataset.RawTechnology]) -> tuple[list[CompiledTech], int]` — but `compile_all` is written in Task 4 (once `dataset.RawTechnology` exists); Task 3 delivers the adapter + `try_compile` only.

- [ ] **Step 1: Write the failing test** — `apps/platform/src/recon/findings/techdetect/compile_test.py`:
```python
from recon.findings.techdetect import compile as tc


def test_compile_and_search_capture_group():
    pattern = tc.compile_pattern(r"nginx(?:/([\d.]+))?")
    match = pattern.search("nginx/1.25.3")
    assert match is not None
    assert match.groups() == ("1.25.3",)


def test_compile_is_case_insensitive_by_default():
    # HTTP header/token matching is case-insensitive (Wappalyzer default).
    assert tc.compile_pattern("express").search("X-Powered-By: Express") is not None


def test_try_compile_returns_none_for_a_lookbehind_pattern():
    # RE2 rejects lookbehind at compile time (T4) -> a soft skip, never a raise.
    assert tc.try_compile(r"(?<!elo\.io)/cargo\.") is None


def test_try_compile_returns_a_pattern_for_a_valid_source():
    compiled = tc.try_compile(r"jquery(?:-([\d.]+))?\.js")
    assert compiled is not None
    assert compiled.search("jquery-3.5.1.js") is not None
```
- [ ] **Step 2: Run test to verify it fails** — first wire the dependency:
  add `"google-re2==1.1.20251105",` to `[project].dependencies` in `apps/platform/pyproject.toml` (with a comment), then `uv lock` and `uv sync --extra dev`. Then:
  `RECON_REQUIRE_ENGINES=1 uv run pytest -m "not integration" src/recon/findings/techdetect/compile_test.py -v`
  Expected: `ImportError: cannot import name 'compile' from 'recon.findings.techdetect'` (module absent).
  The pyproject dependency edit:
```toml
    # Linear-time, ReDoS-safe regex for the untrusted enthec fingerprint patterns
    # (recon.findings.techdetect). Ships win_amd64/win32 + manylinux wheels; pinned
    # so a bad pattern's compile-time behavior (per-pattern try/except) stays stable.
    "google-re2==1.1.20251105",
```
- [ ] **Step 3: Write minimal implementation** — `techdetect/compile.py`:
```python
"""A typed adapter over ``google-re2`` + a defensive per-pattern compiler.

``google-re2`` ships no type stubs, so ``import re2`` resolves to ``Any`` under
``ignore_missing_imports``. This module wraps it behind ``Protocol`` types and one
``cast`` so the rest of ``recon.findings.techdetect`` stays mypy-strict (T8). Every
pattern is compiled through ``try_compile``: RE2 rejects some enthec constructs
(lookbehind, backreferences) at compile time, and a reject must be SKIPPED + counted,
never fatal to the whole dataset (T4)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

import re2  # google-re2 — untyped; wrapped behind the Protocols below

from recon.observability import get_logger

if TYPE_CHECKING:
    from recon.findings.techdetect import dataset

log = get_logger("recon.findings.techdetect.compile")


class Re2Match(Protocol):
    def group(self, index: int = 0, /) -> str: ...
    def groups(self) -> tuple[str | None, ...]: ...


class Re2Pattern(Protocol):
    def search(self, text: str, /) -> Re2Match | None: ...


def compile_pattern(source: str, *, case_insensitive: bool = True) -> Re2Pattern:
    """Compile one pattern under RE2, case-insensitive by default. May raise
    ``re2.error`` on a pattern RE2 rejects — callers use ``try_compile`` instead."""
    options = re2.Options()
    options.case_sensitive = not case_insensitive
    return cast("Re2Pattern", re2.compile(source, options=options))


def try_compile(source: str) -> Re2Pattern | None:
    """Compile ``source`` or return ``None`` if RE2 rejects it (lookbehind /
    backreference / bad syntax). The reject is logged at debug and counted by the
    caller — the dataset load is never all-or-nothing (T4)."""
    try:
        return compile_pattern(source)
    except re2.error as exc:  # google-re2 raises re2.error (drop-in for re.error)
        log.debug("techdetect.pattern_skipped", source=source, error=str(exc))
        return None


@dataclass(frozen=True)
class CompiledPattern:
    """One compiled fingerprint pattern bound to the signal surface it matches."""

    surface: str  # "headers" | "cookies" | "scriptSrc" | "scripts" | "meta"
    key: str | None  # header/cookie/meta NAME (lowercased); None for scriptSrc/scripts
    regex: Re2Pattern
    version_template: str | None
    confidence: int


@dataclass(frozen=True)
class CompiledTech:
    name: str
    categories: tuple[int, ...]
    patterns: tuple[CompiledPattern, ...]


def compile_all(
    raw_techs: dict[str, dataset.RawTechnology],
) -> tuple[list[CompiledTech], int]:
    """Compile every fingerprint field of every technology, skipping (and counting)
    RE2-rejected patterns. Placeholder in Task 3 — implemented in Task 4 once
    ``dataset.RawTechnology`` exists."""
    raise NotImplementedError  # Task 4
```
- [ ] **Step 4: Run test to verify it passes** — from `apps/platform`:
  `RECON_REQUIRE_ENGINES=1 uv run pytest -m "not integration" src/recon/findings/techdetect/compile_test.py -v`
  Expected: 4 passed. Also `uv run mypy src/recon/findings/techdetect/compile.py` — no errors. Also `uv run ruff check src/recon/findings/techdetect`.
- [ ] **Step 5: Commit** —
  `git add apps/platform/pyproject.toml apps/platform/uv.lock apps/platform/src/recon/findings/techdetect/compile.py apps/platform/src/recon/findings/techdetect/compile_test.py`
```
git commit -m "feat(techdetect): typed google-re2 adapter + defensive per-pattern compile

Adds google-re2 (pinned, win+linux wheels) and a Protocol-typed wrapper so
recon.findings.* stays mypy-strict. try_compile skips + logs a pattern RE2 rejects
(lookbehind/backreference) instead of failing the whole dataset (T4).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `techdetect/dataset.py` + `refresh.py` + vendored dataset + `compile_all`
**Files:**
- Create `apps/platform/src/recon/findings/techdetect_data/__init__.py` (empty package marker)
- Create `apps/platform/src/recon/findings/techdetect_data/technologies.json` (curated real subset)
- Create `apps/platform/src/recon/findings/techdetect_data/categories.json`
- Create `apps/platform/src/recon/findings/techdetect_data/commit.txt`
- Create `apps/platform/src/recon/findings/techdetect/dataset.py`
- Create `apps/platform/src/recon/findings/techdetect/refresh.py`
- Modify `apps/platform/src/recon/findings/techdetect/compile.py` (implement `compile_all`)
- Test: `apps/platform/src/recon/findings/techdetect/dataset_test.py`

**Interfaces:** Produces `dataset.RawTechnology` (TypedDict, `total=False`), `dataset.load_raw() -> tuple[dict[str, RawTechnology], dict[str, str], str]` (technologies, category-id→name, pinned commit; lru-cached; fail-closed), and completes `compile.compile_all(raw_techs) -> tuple[list[CompiledTech], int]`. Consumed by `match.py` and `__init__.detect` (Task 5).

- [ ] **Step 1: Write the failing test** — `apps/platform/src/recon/findings/techdetect/dataset_test.py`:
```python
from recon.findings.techdetect import compile as tc
from recon.findings.techdetect import dataset


def test_load_raw_returns_technologies_categories_and_commit():
    techs, categories, commit = dataset.load_raw()
    assert "nginx" in techs
    assert techs["nginx"]["cats"]  # non-empty category id list
    assert categories["22"] == "Web servers"
    assert isinstance(commit, str) and commit


def test_category_names_resolves_ids_to_names():
    _techs, categories, _commit = dataset.load_raw()
    names = dataset.category_names([12, 59], categories)
    assert names == ["JavaScript frameworks", "JavaScript libraries"]


def test_load_is_cached_same_object():
    assert dataset.load_raw() is dataset.load_raw()


def test_compile_all_loads_all_and_reports_a_bounded_skip_count():
    techs, _categories, _commit = dataset.load_raw()
    compiled, skipped = tc.compile_all(techs)
    assert len(compiled) > 0
    # The curated subset is RE2-safe; a future full re-pin (refresh.py) keeps rejects
    # well under this bound — the load is never all-or-nothing (T4).
    assert skipped <= 40
```
- [ ] **Step 2: Run test to verify it fails** — from `apps/platform`:
  `RECON_REQUIRE_ENGINES=1 uv run pytest -m "not integration" src/recon/findings/techdetect/dataset_test.py -v`
  Expected: `ModuleNotFoundError: No module named 'recon.findings.techdetect.dataset'` (and `compile_all` raises `NotImplementedError`).
- [ ] **Step 3: Write minimal implementation.**
  Create `techdetect_data/__init__.py` (empty). Create `techdetect_data/commit.txt` with content `curated-subset` (refresh.py overwrites it with the real enthec commit sha on a network re-pin). Create `techdetect_data/categories.json`:
```json
{
  "1":  {"name": "CMS"},
  "6":  {"name": "Ecommerce"},
  "12": {"name": "JavaScript frameworks"},
  "18": {"name": "Web frameworks"},
  "22": {"name": "Web servers"},
  "23": {"name": "Caching"},
  "27": {"name": "Programming languages"},
  "31": {"name": "CDN"},
  "59": {"name": "JavaScript libraries"},
  "62": {"name": "PaaS"}
}
```
  Create `techdetect_data/technologies.json` (a curated, RE2-safe real subset — expand later via refresh.py):
```json
{
  "Nginx":       {"cats": [22], "headers": {"Server": "nginx(?:/([\\d.]+))?\\;version:\\1"}},
  "Apache":      {"cats": [22], "headers": {"Server": "(?:Apache(?:$|/([\\d.]+)|[^/-])|HTTPD)\\;version:\\1"}},
  "Express":     {"cats": [18], "headers": {"X-Powered-By": "^Express$"}},
  "PHP":         {"cats": [27], "headers": {"X-Powered-By": "PHP(?:/([\\d.]+))?\\;version:\\1"}},
  "ASP.NET":     {"cats": [18], "headers": {"X-Powered-By": "ASP\\.NET", "X-AspNet-Version": "([\\d.]+)\\;version:\\1"}},
  "WordPress":   {"cats": [1],  "meta": {"generator": "WordPress ?([\\d.]+)?\\;version:\\1"}},
  "Drupal":      {"cats": [1],  "headers": {"X-Drupal-Dynamic-Cache": "", "X-Generator": "Drupal ?([\\d.]+)?\\;version:\\1"}},
  "jQuery":      {"cats": [59], "scriptSrc": ["jquery(?:-([\\d.]+))?(?:\\.min)?\\.js\\;version:\\1"]},
  "React":       {"cats": [12], "scriptSrc": ["react(?:\\.min)?\\.js", "react-dom(?:\\.min)?\\.js"]},
  "Vue.js":      {"cats": [12], "scriptSrc": ["vue(?:@([\\d.]+))?(?:\\.min)?\\.js\\;version:\\1"]},
  "Cloudflare":  {"cats": [31], "headers": {"cf-ray": "", "Server": "cloudflare"}},
  "Varnish":     {"cats": [23], "headers": {"Via": "varnish(?: \\(Varnish/([\\d.]+)\\))?\\;version:\\1", "X-Varnish": ""}},
  "Fastly":      {"cats": [31], "headers": {"X-Served-By": "cache-.+", "Via": ".*fastly.*"}},
  "Amazon CloudFront": {"cats": [31], "headers": {"X-Amz-Cf-Id": "", "Via": ".*CloudFront.*"}},
  "Shopify":     {"cats": [6],  "headers": {"X-Shopify-Stage": ""}},
  "GitHub Pages":{"cats": [62], "headers": {"X-GitHub-Request-Id": ""}}
}
```
  Create `techdetect/dataset.py`:
```python
"""Load the vendored enthec/webappanalyzer fingerprint dataset (GPL-3.0, server-side
only — T10). Package-data JSON, lru-cached. Fail-closed: a missing/corrupt dataset
raises (the analyze pass swallows it at runtime; a load-time test guarantees presence,
NOT the test-only RECON_REQUIRE_ENGINES flag — T7)."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import TypedDict, cast

_DATA_PACKAGE = "recon.findings.techdetect_data"


class RawTechnology(TypedDict, total=False):
    cats: list[int]
    headers: dict[str, str]
    cookies: dict[str, str]
    scriptSrc: list[str]
    scripts: list[str]
    meta: dict[str, str]
    js: dict[str, str]
    html: list[str]
    implies: list[str]
    website: str


@lru_cache(maxsize=1)
def load_raw() -> tuple[dict[str, RawTechnology], dict[str, str], str]:
    """Return (technologies, category-id -> name, pinned commit). lru-cached."""
    files = resources.files(_DATA_PACKAGE)
    techs = cast(
        "dict[str, RawTechnology]",
        json.loads(files.joinpath("technologies.json").read_text(encoding="utf-8")),
    )
    raw_categories = cast(
        "dict[str, dict[str, object]]",
        json.loads(files.joinpath("categories.json").read_text(encoding="utf-8")),
    )
    categories = {cid: str(entry["name"]) for cid, entry in raw_categories.items()}
    commit = files.joinpath("commit.txt").read_text(encoding="utf-8").strip()
    return techs, categories, commit


def category_names(cats: list[int], categories: dict[str, str]) -> list[str]:
    """Resolve enthec numeric category ids to display names, dropping unknown ids."""
    return [categories[str(cid)] for cid in cats if str(cid) in categories]
```
  Create `techdetect/refresh.py`:
```python
"""Re-pin the vendored enthec/webappanalyzer dataset (manual, Phase 1).

Run: ``uv run python -m recon.findings.techdetect.refresh <ref>`` — fetches the
enthec/webappanalyzer ``src/technologies/*.json`` + ``src/categories.json`` at a git
ref, merges them into the vendored ``technologies.json`` / ``categories.json``, and
writes the pinned sha to ``commit.txt``. GPL-3.0 stays server-side (T10). Network is
used ONLY here, never at request time; the load path (dataset.py) is offline."""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

_RAW = "https://raw.githubusercontent.com/enthec/webappanalyzer/{ref}/src"
_LETTERS = "_abcdefghijklmnopqrstuvwxyz"
_DATA_DIR = Path(__file__).resolve().parent.parent / "techdetect_data"


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 - fixed host, manual tool
        return bytes(resp.read())


def refresh(ref: str) -> None:
    merged: dict[str, object] = {}
    for letter in _LETTERS:
        merged.update(json.loads(_get(f"{_RAW.format(ref=ref)}/technologies/{letter}.json")))
    (_DATA_DIR / "technologies.json").write_text(
        json.dumps(merged, ensure_ascii=False, sort_keys=True, indent=1), encoding="utf-8"
    )
    (_DATA_DIR / "categories.json").write_bytes(_get(f"{_RAW.format(ref=ref)}/categories.json"))
    (_DATA_DIR / "commit.txt").write_text(ref, encoding="utf-8")
    print(f"pinned enthec dataset at {ref}: {len(merged)} technologies")


if __name__ == "__main__":
    refresh(sys.argv[1] if len(sys.argv) > 1 else "master")
```
  Replace `compile_all`'s body in `techdetect/compile.py` (remove `NotImplementedError`, remove the `TYPE_CHECKING` guard's need if you now import dataset lazily — keep the `TYPE_CHECKING` import for the annotation and import `version` at top):
```python
from recon.findings.techdetect import version  # add to compile.py imports
```
```python
def compile_all(
    raw_techs: dict[str, dataset.RawTechnology],
) -> tuple[list[CompiledTech], int]:
    """Compile every fingerprint field of every technology into CompiledPatterns,
    skipping (and counting) RE2-rejected patterns (T4). Only the Phase-1 surfaces are
    compiled — headers, cookies, scriptSrc, scripts, meta; ``js``/``html`` are Phase 2."""
    compiled: list[CompiledTech] = []
    skipped = 0
    for name, tech in raw_techs.items():
        patterns: list[CompiledPattern] = []
        skipped += _compile_mapping(patterns, "headers", tech.get("headers"))
        skipped += _compile_mapping(patterns, "cookies", tech.get("cookies"))
        skipped += _compile_mapping(patterns, "meta", tech.get("meta"))
        skipped += _compile_list(patterns, "scriptSrc", tech.get("scriptSrc"))
        skipped += _compile_list(patterns, "scripts", tech.get("scripts"))
        compiled.append(
            CompiledTech(
                name=name,
                categories=tuple(tech.get("cats", [])),
                patterns=tuple(patterns),
            )
        )
    return compiled, skipped


def _compile_mapping(out: list[CompiledPattern], surface: str, mapping: dict[str, str] | None) -> int:
    """Compile a name->pattern mapping (headers/cookies/meta). Returns the skip count."""
    if not mapping:
        return 0
    skipped = 0
    for key, raw in mapping.items():
        regex_source, tags = version.parse_field_value(raw or "")
        regex = try_compile(regex_source or "")  # "" (cookie presence) compiles to match-all
        if regex is None:
            skipped += 1
            continue
        out.append(
            CompiledPattern(
                surface=surface,
                key=key.lower(),
                regex=regex,
                version_template=tags.version,
                confidence=tags.confidence,
            )
        )
    return skipped


def _compile_list(out: list[CompiledPattern], surface: str, values: list[str] | None) -> int:
    """Compile a list of patterns (scriptSrc/scripts). Returns the skip count."""
    if not values:
        return 0
    skipped = 0
    for raw in values:
        regex_source, tags = version.parse_field_value(raw)
        regex = try_compile(regex_source)
        if regex is None:
            skipped += 1
            continue
        out.append(
            CompiledPattern(
                surface=surface,
                key=None,
                regex=regex,
                version_template=tags.version,
                confidence=tags.confidence,
            )
        )
    return skipped
```
  Add the package-data + refresh note handled in Task 11 (pyproject) — for now `importlib.resources` reads the source tree directly in dev/test, so the test passes without the wheel change.
- [ ] **Step 4: Run test to verify it passes** — from `apps/platform`:
  `RECON_REQUIRE_ENGINES=1 uv run pytest -m "not integration" src/recon/findings/techdetect/dataset_test.py src/recon/findings/techdetect/compile_test.py -v`
  Expected: all passed (4 dataset + 4 compile). Also `uv run mypy src/recon/findings/techdetect/dataset.py src/recon/findings/techdetect/compile.py src/recon/findings/techdetect/refresh.py` — no errors.
- [ ] **Step 5: Commit** —
  `git add apps/platform/src/recon/findings/techdetect_data apps/platform/src/recon/findings/techdetect/dataset.py apps/platform/src/recon/findings/techdetect/refresh.py apps/platform/src/recon/findings/techdetect/compile.py apps/platform/src/recon/findings/techdetect/dataset_test.py`
```
git commit -m "feat(techdetect): vendored enthec dataset loader + refresh + compile_all

Vendors a curated, RE2-safe enthec/webappanalyzer subset (technologies +
categories + pinned commit) as package-data, lru-cached and fail-closed (T7).
refresh.py re-pins it from upstream (manual, network only here). compile_all
compiles every Phase-1 surface (headers/cookies/meta/scriptSrc/scripts) and
reports a bounded skip count, asserted by the load-time test (T4).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: `techdetect/match.py` + `__init__.detect`
**Files:**
- Create `apps/platform/src/recon/findings/techdetect/match.py`
- Modify `apps/platform/src/recon/findings/techdetect/__init__.py` (the public surface)
- Test: `apps/platform/src/recon/findings/techdetect/match_test.py`

**Interfaces:** Consumes `compile.compile_all`, `compile.CompiledTech`, `dataset.load_raw`, `dataset.category_names`, `version.resolve_version`. Produces the package's public API:
- `techdetect.Detection` (frozen dataclass: `name: str`, `categories: list[str]`, `version: str | None`, `confidence: int`, `evidence: list[str]`)
- `techdetect.detect(host: str, signal: dict[str, Any], js_texts: list[str]) -> list[Detection]`
- `techdetect.dataset_commit() -> str`
- `techdetect.skipped_pattern_count() -> int`

The `signal` dict is one host's value from the fingerprint-signal blob: `{"headers": dict[str,str], "scripts": list[str], "meta": list[str], "cookies": list[str]}`. Confidence is summed across matching patterns, capped at 100; on conflicting versions the highest-confidence pattern's version wins and alternates ride in `evidence` (T3).

- [ ] **Step 1: Write the failing test** — `apps/platform/src/recon/findings/techdetect/match_test.py`:
```python
from recon.findings import techdetect


def _signal(**over):
    base = {"headers": {}, "scripts": [], "meta": [], "cookies": []}
    base.update(over)
    return base


def test_detects_server_framework_library_and_meta_with_versions():
    signal = _signal(
        headers={"server": "nginx/1.25.3", "x-powered-by": "Express"},
        scripts=["https://acme.io/static/jquery-3.5.1.min.js"],
        meta=["WordPress 6.4"],
    )
    names = {d.name: d for d in techdetect.detect("acme.io", signal, [])}
    assert names["Nginx"].version == "1.25.3"
    assert names["Nginx"].categories == ["Web servers"]
    assert "Express" in names
    assert names["jQuery"].version == "3.5.1"
    assert names["WordPress"].version == "6.4"


def test_confidence_sums_across_patterns_capped_at_100():
    # Cloudflare matches on BOTH cf-ray (100) and Server:cloudflare (100) -> capped 100.
    signal = _signal(headers={"cf-ray": "7d1b-EWR", "server": "cloudflare"})
    cloudflare = next(d for d in techdetect.detect("acme.io", signal, []) if d.name == "Cloudflare")
    assert cloudflare.confidence == 100
    assert len(cloudflare.evidence) == 2  # both surfaces recorded


def test_evidence_is_bounded_and_secret_free():
    signal = _signal(headers={"server": "nginx/1.25.3"})
    nginx = next(d for d in techdetect.detect("acme.io", signal, []) if d.name == "Nginx")
    assert nginx.evidence == ["server: nginx/1.25.3"]
    assert all(len(e) <= 200 for e in nginx.evidence)


def test_no_signal_yields_no_detections():
    assert techdetect.detect("acme.io", _signal(), []) == []


def test_dataset_commit_and_skip_count_are_exposed():
    assert isinstance(techdetect.dataset_commit(), str)
    assert techdetect.skipped_pattern_count() >= 0
```
- [ ] **Step 2: Run test to verify it fails** — from `apps/platform`:
  `RECON_REQUIRE_ENGINES=1 uv run pytest -m "not integration" src/recon/findings/techdetect/match_test.py -v`
  Expected: `AttributeError: module 'recon.findings.techdetect' has no attribute 'detect'`.
- [ ] **Step 3: Write minimal implementation** — `techdetect/match.py`:
```python
"""Apply compiled fingerprint patterns to one host's signal + JS. Pure.

Confidence is SUMMED across every matching pattern of a technology and capped at 100
(enthec confidences are designed to combine toward 100 — T3). On conflicting versions
the highest-confidence match's version wins; the alternates are kept in ``evidence``.
``implies``/``requires``/``excludes`` are NOT followed in Phase 1 (flat list)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from recon.findings.techdetect import version as version_mod
from recon.findings.techdetect.compile import CompiledPattern, CompiledTech

_EVIDENCE_MAX = 200  # a bounded marker snippet — never a full body (T1)


@dataclass(frozen=True)
class Detection:
    name: str
    categories: list[str]
    version: str | None
    confidence: int
    evidence: list[str]


@dataclass
class _Acc:
    confidence: int = 0
    version: str | None = None
    version_confidence: int = 0  # confidence of the pattern that set `version`
    evidence: list[str] = field(default_factory=list)


def match(
    compiled: list[CompiledTech],
    categories: dict[str, str],
    host: str,
    signal: dict[str, Any],
    js_texts: list[str],
) -> list[Detection]:
    from recon.findings.techdetect.dataset import category_names

    accs: dict[str, _Acc] = {}
    cats_by_name: dict[str, tuple[int, ...]] = {}
    for tech in compiled:
        for pattern in tech.patterns:
            for value in _surface_values(pattern, signal, js_texts):
                found = pattern.regex.search(value)
                if found is None:
                    continue
                cats_by_name[tech.name] = tech.categories
                _record(accs.setdefault(tech.name, _Acc()), pattern, found, value)
    return [
        Detection(
            name=name,
            categories=category_names(list(cats_by_name.get(name, ())), categories),
            version=acc.version,
            confidence=min(acc.confidence, 100),
            evidence=acc.evidence,
        )
        for name, acc in sorted(accs.items())
    ]


def _surface_values(pattern: CompiledPattern, signal: dict[str, Any], js_texts: list[str]) -> list[str]:
    """The candidate strings a pattern is searched against, per its surface."""
    if pattern.surface == "headers":
        value = signal.get("headers", {}).get(pattern.key)
        return [value] if value else []
    if pattern.surface == "cookies":
        # enthec cookie patterns test presence of the NAME (value regex usually empty).
        return [pattern.key or ""] if (pattern.key in signal.get("cookies", [])) else []
    if pattern.surface == "scriptSrc":
        return list(signal.get("scripts", []))
    if pattern.surface == "meta":
        return list(signal.get("meta", [])) if pattern.key == "generator" else []
    if pattern.surface == "scripts":
        return js_texts
    return []


def _record(acc: _Acc, pattern: CompiledPattern, found: Any, value: str) -> None:
    acc.confidence += pattern.confidence
    resolved = version_mod.resolve_version(pattern.version_template, found.groups())
    if resolved and pattern.confidence >= acc.version_confidence:
        if acc.version and acc.version != resolved:
            acc.evidence.append(f"version alt: {acc.version}")
        acc.version = resolved
        acc.version_confidence = pattern.confidence
    marker = f"{pattern.key or pattern.surface}: {value}"
    acc.evidence.append(marker[:_EVIDENCE_MAX])
```
  Write `techdetect/__init__.py`:
```python
"""In-house pure-Python technology fingerprinter over the vendored enthec dataset.

``detect(host, signal, js_texts)`` matches the dataset's Phase-1 surfaces (response
headers, cookie names, script URLs, ``<meta generator>``, and JS source via the
``scripts`` field) with ``google-re2`` (ReDoS-safe). No network, no secret storage:
input is only the allowlisted fingerprint-signal + already-stored JS bytes."""

from __future__ import annotations

from typing import Any

from recon.findings.techdetect import compile as _compile
from recon.findings.techdetect import dataset as _dataset
from recon.findings.techdetect import match as _match
from recon.findings.techdetect.match import Detection

__all__ = ["Detection", "detect", "dataset_commit", "skipped_pattern_count"]


def detect(host: str, signal: dict[str, Any], js_texts: list[str]) -> list[Detection]:
    techs, categories, _commit = _dataset.load_raw()
    compiled, _skipped = _compile.compile_all(techs)
    return _match.match(compiled, categories, host, signal, js_texts)


def dataset_commit() -> str:
    return _dataset.load_raw()[2]


def skipped_pattern_count() -> int:
    return _compile.compile_all(_dataset.load_raw()[0])[1]
```
  Note: `compile.compile_all` is lru-cache-worthy but is cheap and pure; to avoid recompiling per host, wrap it with `@lru_cache` keyed on nothing is impossible (dict arg unhashable). Instead memoize in `__init__` with a module-level cache:
```python
# add to techdetect/__init__.py, replacing the direct compile_all calls
from functools import lru_cache


@lru_cache(maxsize=1)
def _compiled() -> tuple[list, int]:
    techs, _categories, _commit = _dataset.load_raw()
    return _compile.compile_all(techs)
```
  and use `_compiled()` in `detect` (`compiled, _ = _compiled()`) and `skipped_pattern_count` (`return _compiled()[1]`).
- [ ] **Step 4: Run test to verify it passes** — from `apps/platform`:
  `RECON_REQUIRE_ENGINES=1 uv run pytest -m "not integration" src/recon/findings/techdetect -v`
  Expected: all techdetect tests pass (version + compile + dataset + match). Also `uv run mypy src/recon/findings/techdetect` and `uv run ruff check src/recon/findings/techdetect` — clean.
- [ ] **Step 5: Commit** —
  `git add apps/platform/src/recon/findings/techdetect/match.py apps/platform/src/recon/findings/techdetect/__init__.py apps/platform/src/recon/findings/techdetect/match_test.py`
```
git commit -m "feat(techdetect): host matcher + detect() public API

match() applies the compiled dataset to one host's signal + JS, summing confidence
per technology (capped 100) and keeping the highest-confidence version with alternates
in bounded, secret-free evidence (T1/T3). detect(host, signal, js_texts) is the
package entrypoint; dataset_commit()/skipped_pattern_count() feed the analyze event.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Fetch signal harvest (shared hop-core, no `fetch_url` signature change)
**Files:**
- Modify `apps/platform/src/recon/fetch/fetch.py` (extract `_fetch_hops`; `fetch_url` becomes a thin wrapper ~lines 110-170; harvest in `_fetch_assets` ~lines 302-404)
- Test: `apps/platform/src/recon/fetch/fetch_signal_test.py`

**Interfaces:** Produces `fetch._FetchedResponse(body: bytes, status: int, headers: dict[str, str], set_cookie: list[str])`, `fetch._fetch_hops(...) -> _FetchedResponse` (same params as `fetch_url`), and the module constant `fetch._HEADER_ALLOWLIST`. `fetch_url`'s public signature is UNCHANGED — it returns `_fetch_hops(...).body` (T5). Emits a `fingerprint.signal` RunEvent (`{signal_ref, hosts}`) indexing one `"fingerprint-signal"` blob per run (T6), consumed by Task 8. The blob JSON schema per host: `{"headers": {...}, "scripts": [...urls], "meta": [], "cookies": [names]}`.

- [ ] **Step 1: Write the failing test** — `apps/platform/src/recon/fetch/fetch_signal_test.py` (pure; httpx MockTransport, no infra):
```python
import httpx

from recon.fetch import fetch


def _transport(headers: dict[str, str]):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers=headers, content=b"console.log(1)")
    return httpx.MockTransport(handler)


def test_fetch_hops_returns_body_status_and_headers():
    resp = fetch._fetch_hops(
        "https://acme.io/app.js", ["acme.io"], timeout_s=5, max_bytes=1_000_000,
        transport=_transport({"Server": "nginx/1.25.3", "X-Powered-By": "Express"}),
    )
    assert resp.body == b"console.log(1)"
    assert resp.status == 200
    assert resp.headers["server"] == "nginx/1.25.3"


def test_fetch_url_still_returns_bytes_unchanged():
    body = fetch.fetch_url(
        "https://acme.io/app.js", ["acme.io"], timeout_s=5, max_bytes=1_000_000,
        transport=_transport({"Server": "nginx"}),
    )
    assert body == b"console.log(1)"


def test_allowlist_keeps_fingerprint_headers_and_drops_the_rest():
    kept = fetch._allowlisted_headers(
        {"server": "nginx", "x-powered-by": "Express", "authorization": "Bearer x",
         "set-cookie": "sid=abc", "x-fastly-request-id": "r1"}
    )
    assert kept == {"server": "nginx", "x-powered-by": "Express", "x-fastly-request-id": "r1"}
    assert "authorization" not in kept and "set-cookie" not in kept


def test_cookie_names_never_carry_values():
    assert fetch._cookie_names(["sid=SECRETVALUE; Path=/", "theme=dark"]) == ["sid", "theme"]
```
- [ ] **Step 2: Run test to verify it fails** — from `apps/platform`:
  `RECON_REQUIRE_ENGINES=1 uv run pytest -m "not integration" src/recon/fetch/fetch_signal_test.py -v`
  Expected: `AttributeError: module 'recon.fetch.fetch' has no attribute '_fetch_hops'` (and `_allowlisted_headers`, `_cookie_names`).
- [ ] **Step 3: Write minimal implementation.**
  In `fetch.py`, add imports at top: `import json` and `from dataclasses import dataclass`, `from recon.events.log import publish, record_event`. Add the allowlist + helpers near the top (after `_FETCH_HEADERS`):
```python
# Allowlisted response headers for tech detection (case-insensitive). VALUES of any
# header NOT in this set are discarded; Set-Cookie contributes NAMES only (T1). No
# credential-bearing header (Authorization, Cookie) is ever persisted.
_HEADER_ALLOWLIST = frozenset(
    {
        "server", "x-powered-by", "x-aspnet-version", "x-aspnetmvc-version",
        "x-generator", "x-drupal-dynamic-cache", "x-drupal-cache", "via",
        "x-varnish", "cf-ray", "x-amz-cf-id", "x-served-by", "x-shopify-stage",
        "x-github-request-id",
    }
)


@dataclass(frozen=True)
class _FetchedResponse:
    body: bytes
    status: int
    headers: dict[str, str]  # final response headers, lowercased keys
    set_cookie: list[str]  # raw Set-Cookie lines; NAMES extracted by _cookie_names


def _allowlisted_headers(headers: dict[str, str]) -> dict[str, str]:
    kept = {name: headers[name] for name in _HEADER_ALLOWLIST if name in headers}
    kept.update({k: v for k, v in headers.items() if k.startswith("x-fastly-")})
    return kept


def _cookie_names(set_cookie_lines: list[str]) -> list[str]:
    """Cookie NAMES only, never values (T1) — the token before the first '='."""
    names = {line.split("=", 1)[0].strip() for line in set_cookie_lines if "=" in line}
    return sorted(n for n in names if n)
```
  Replace `fetch_url` (lines 110-170) with `_fetch_hops` + a thin `fetch_url` wrapper. Keep the loop body identical; change only the success return and add the wrapper:
```python
def _fetch_hops(
    url: str,
    scope_hosts: list[str],
    *,
    timeout_s: float,
    max_bytes: int,
    max_redirects: int = _MAX_REDIRECTS,
    allow_local: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> _FetchedResponse:
    """Fetch ``url`` under the full egress policy and return body + status + the
    allowlist-source headers. The shared validated-hop core: ``fetch_url`` is the
    thin bytes-only wrapper (its public signature is unchanged — the SSRF crown jewel
    is not churned, T5). Same exceptions as ``fetch_url``."""
    deadline = time.monotonic() + timeout_s
    current = url
    with httpx.Client(
        follow_redirects=False, timeout=httpx.Timeout(timeout_s), transport=transport
    ) as client:
        for _hop in range(max_redirects + 1):
            target = egress.validate_target(current, scope_hosts, allow_local=allow_local)
            if httpx.URL(current).host.lower() != target.host.lower():
                raise egress.EgressBlocked(
                    f"URL host parse mismatch: {httpx.URL(current).host} vs {target.host}"
                )
            with (
                _pin_dns(target.host, target.ips),
                client.stream("GET", current, headers=_FETCH_HEADERS) as response,
            ):
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise retry.FatalError("redirect without a Location header")
                    current = urljoin(current, location)
                    continue
                if not 200 <= response.status_code < 300:
                    message = f"target returned HTTP {response.status_code}"
                    if retry.http_retryable(response.status_code):
                        retry_after = _parse_retry_after(response.headers.get("retry-after"))
                        raise retry.RetryableError(message, retry_after=retry_after)
                    raise retry.FatalError(message)
                body = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() > deadline:
                        raise retry.RetryableError("overall fetch deadline exceeded")
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise retry.FatalError(f"response exceeds {max_bytes} bytes")
                return _FetchedResponse(
                    body=bytes(body),
                    status=response.status_code,
                    headers={k.lower(): v for k, v in response.headers.items()},
                    set_cookie=list(response.headers.get_list("set-cookie")),
                )
    raise retry.FatalError(f"exceeded {max_redirects} redirects")


def fetch_url(
    url: str,
    scope_hosts: list[str],
    *,
    timeout_s: float,
    max_bytes: int,
    max_redirects: int = _MAX_REDIRECTS,
    allow_local: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> bytes:
    """Fetch ``url`` under the egress policy and return its bytes. Thin wrapper over
    ``_fetch_hops`` — signature and behavior unchanged (T5)."""
    return _fetch_hops(
        url,
        scope_hosts,
        timeout_s=timeout_s,
        max_bytes=max_bytes,
        max_redirects=max_redirects,
        allow_local=allow_local,
        transport=transport,
    ).body
```
  In `_fetch_assets`, harvest per asset and write ONE blob after the loop. Change the JS fetch call to `_fetch_hops` and accumulate a `signal` dict; after the loop, persist. Concretely: initialize `signal: dict[str, dict] = {}` before the loop; where the code currently calls `content = fetch_url(asset.url, ...)`, use:
```python
            try:
                fetched = _fetch_hops(
                    asset.url,
                    engagement.scope_hosts,
                    timeout_s=settings.fetch_timeout_seconds,
                    max_bytes=cap,
                    allow_local=settings.allow_local_egress,
                )
                content = fetched.body
            except (egress.EgressBlocked, retry.FatalError, retry.RetryableError) as exc:
                ...  # unchanged failure branch
            _harvest_signal(signal, asset.url, fetched)  # accumulate the per-host signal
```
  (Keep the existing `content = ...` usage for `put_blob`/source-map unchanged; just source it from `fetched.body`.) After the `for` loop ends, before the function returns, add:
```python
    _write_fingerprint_signal(redis, tenant_id=tenant_id, run_id=run_id, signal=signal)
```
  Add the two helpers:
```python
def _harvest_signal(signal: dict[str, dict], asset_url: str, fetched: _FetchedResponse) -> None:
    """Fold one asset's allowlisted headers + cookie names + script URL into the
    per-host signal (T1). Host from the OBSERVED asset URL (T11)."""
    host = (urlsplit(asset_url).hostname or "").lower()
    if not host:
        return
    entry = signal.setdefault(host, {"headers": {}, "scripts": [], "meta": [], "cookies": []})
    entry["headers"].update(_allowlisted_headers(fetched.headers))
    for name in _cookie_names(fetched.set_cookie):
        if name not in entry["cookies"]:
            entry["cookies"].append(name)
    if asset_url not in entry["scripts"]:
        entry["scripts"].append(asset_url)


def _write_fingerprint_signal(
    redis: Redis, *, tenant_id: str, run_id: str, signal: dict[str, dict]
) -> None:
    """Persist ONE per-run fingerprint-signal blob + index it with a durable
    ``fingerprint.signal`` event (consolidated once per run — T6). Recorded, not
    published: the sole consumer is the analyze fingerprint pass (durable log)."""
    if not signal:
        return
    signal_ref = storage.put_blob(
        tenant_id, run_id, "fingerprint-signal", json.dumps(signal).encode("utf-8")
    )
    with tenant_session(tenant_id) as session:
        record_event(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            event_type="fingerprint.signal",
            payload={"signal_ref": signal_ref, "hosts": len(signal)},
        )
```
- [ ] **Step 4: Run test to verify it passes** — from `apps/platform`:
  `RECON_REQUIRE_ENGINES=1 uv run pytest -m "not integration" src/recon/fetch/fetch_signal_test.py src/recon/fetch/fetch_test.py src/recon/fetch/fetch_multi_test.py -v`
  Expected: new tests pass and the existing fetch tests still pass (fetch_url unchanged). `uv run ruff check src/recon/fetch/fetch.py`.
- [ ] **Step 5: Commit** —
  `git add apps/platform/src/recon/fetch/fetch.py apps/platform/src/recon/fetch/fetch_signal_test.py`
```
git commit -m "feat(fetch): harvest an allowlisted per-run fingerprint signal

Extracts a shared validated-hop core (_fetch_hops) exposing body+status+headers;
fetch_url stays a thin bytes-only wrapper so the SSRF path is not churned (T5).
_fetch_assets folds each asset's allowlisted headers + cookie NAMES + script URL
into one host-keyed signal, written as a single fingerprint-signal blob per run and
indexed by a fingerprint.signal event (T1/T6/T11). No values of non-allowlisted
headers, no Set-Cookie values, no raw HTML.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Capture signal harvest (CDP response headers + meta/scriptSrc/cookie names)
**Files:**
- Modify `apps/platform/src/recon/capture/driver.py` (`_REQUEST_TYPES` area ~line 93; `CaptureResult` ~lines 130-142; `_Ctx.__init__` ~lines 379-414; `_route` ~lines 458-490; add `Network.responseReceived` handling + a meta read; surface on `CaptureResult`)
- Modify `apps/platform/src/recon/capture/stage.py` (`capture_run` ~lines 176-207: build + write the fingerprint-signal blob)
- Test: `apps/platform/src/recon/capture/capture_signal_test.py`

**Interfaces:** Consumes nothing new. Produces, on `driver.CaptureResult`, three defaulted fields: `headers_by_host: dict[str, dict[str, str]]`, `cookies_by_host: dict[str, list[str]]`, `meta: list[str]`. `capture/stage.py` writes ONE `"fingerprint-signal"` blob + a `fingerprint.signal` event with the SAME schema and event type as Task 6, so Task 8 reads both crawl and capture runs identically.

- [ ] **Step 1: Write the failing test** — `apps/platform/src/recon/capture/capture_signal_test.py` (pure; drives `_Ctx._route` with fake CDP frames + tests the stage's signal builder):
```python
from recon.capture import stage
from recon.capture.driver import CapturedScript, CaptureResult


def test_stage_builds_host_keyed_signal_from_capture_result():
    result = CaptureResult(
        scripts=[CapturedScript("https://acme.io/app.js", b"x", None, "abc", "page")],
        nav_error=None,
        requests=[],
        headers_by_host={"acme.io": {"server": "nginx/1.25.3"}},
        cookies_by_host={"acme.io": ["sid"]},
        meta=["WordPress 6.4"],
    )
    signal = stage._build_signal(
        result, target_host="acme.io", kept=result.scripts,
    )
    assert signal["acme.io"]["headers"] == {"server": "nginx/1.25.3"}
    assert signal["acme.io"]["scripts"] == ["https://acme.io/app.js"]
    assert signal["acme.io"]["meta"] == ["WordPress 6.4"]
    assert signal["acme.io"]["cookies"] == ["sid"]


def test_signal_is_empty_when_nothing_harvested():
    result = CaptureResult(scripts=[], nav_error=None, requests=[])
    assert stage._build_signal(result, target_host="acme.io", kept=[]) == {}
```
- [ ] **Step 2: Run test to verify it fails** — from `apps/platform`:
  `RECON_REQUIRE_ENGINES=1 uv run pytest -m "not integration" src/recon/capture/capture_signal_test.py -v`
  Expected: `TypeError: CaptureResult.__init__() got an unexpected keyword argument 'headers_by_host'` (fields absent), then `AttributeError: module 'recon.capture.stage' has no attribute '_build_signal'`.
- [ ] **Step 3: Write minimal implementation.**
  In `driver.py`, extend `CaptureResult` with defaulted fields (keep existing fields + `requests`):
```python
    headers_by_host: dict = field(default_factory=dict)  # host -> allowlisted response headers
    cookies_by_host: dict = field(default_factory=dict)  # host -> cookie NAMES (never values)
    meta: list[str] = field(default_factory=list)  # <meta name=generator> content strings
```
  Reuse the fetch allowlist to avoid drift — add to `driver.py` imports: `from recon.fetch.fetch import _allowlisted_headers, _cookie_names`. In `_Ctx.__init__`, initialize:
```python
        self.headers_by_host: dict[str, dict[str, str]] = {}
        self.cookies_by_host: dict[str, list[str]] = {}
        self.meta: list[str] = []
```
  In `_Ctx._route`, add a branch (next to `Network.requestWillBeSent`):
```python
        if method == "Network.responseReceived":
            self._on_response(msg)
            return None
```
  Add the handler on `_Ctx` (mirrors `_on_request`'s total, never-raising style):
```python
    def _on_response(self, msg: dict) -> None:
        """Fold one response's allowlisted headers + cookie names into the per-host
        signal (tech detection). TOTAL — a malformed frame is skipped, never raised,
        so one bad event can't abort capture. Values of non-allowlisted headers and
        Set-Cookie VALUES are discarded here (T1)."""
        params = msg.get("params") or {}
        response = params.get("response") or {}
        url = response.get("url") or ""
        host = (urlsplit(url).hostname or "").lower()
        if not host:
            return
        headers = {str(k).lower(): str(v) for k, v in (response.get("headers") or {}).items()}
        allow = _allowlisted_headers(headers)
        if allow:
            self.headers_by_host.setdefault(host, {}).update(allow)
        set_cookie = headers.get("set-cookie")
        if set_cookie:
            names = self.cookies_by_host.setdefault(host, [])
            for name in _cookie_names(set_cookie.split("\n")):
                if name not in names:
                    names.append(name)
```
  Read `<meta name=generator>` once after the initial settle in `_drive`, before returning the result (the page session already has an `evaluate` helper). After the interaction block and before `return CaptureResult(...)`:
```python
        generator = ctx.evaluate(
            "document.querySelector('meta[name=generator]')?.content || ''",
            timeout_s=2.0,
        )
        if generator and generator.get("value"):
            ctx.meta.append(str(generator["value"]))
```
  Update the final return in `_drive` to pass the new fields:
```python
        return CaptureResult(
            scripts=ctx.out,
            nav_error=ctx.nav_error,
            requests=ctx.requests,
            headers_by_host=ctx.headers_by_host,
            cookies_by_host=ctx.cookies_by_host,
            meta=ctx.meta,
        )
```
  In `stage.py`, add `import` for events already present (`from recon.events.log import publish, record_event` — `record_event`/`publish` already imported). Add the signal builder + a write in `capture_run`. Add the builder function:
```python
def _build_signal(
    result: driver.CaptureResult,
    *,
    target_host: str,
    kept: list[driver.CapturedScript],
) -> dict[str, dict]:
    """Build the host-keyed fingerprint signal from a capture result (same schema as
    the fetch path — T6). Script URLs come from the kept (in-scope) scripts; an
    anonymous/inline script (no URL) is attributed to the target host. ``meta`` is
    attached to the target host (the document it was read from)."""
    signal: dict[str, dict] = {}

    def _entry(host: str) -> dict:
        return signal.setdefault(host, {"headers": {}, "scripts": [], "meta": [], "cookies": []})

    for host, headers in result.headers_by_host.items():
        _entry(host)["headers"].update(headers)
    for host, cookies in result.cookies_by_host.items():
        entry = _entry(host)
        entry["cookies"] = sorted(set(entry["cookies"]) | set(cookies))
    for script in kept:
        host = (urlsplit(script.url).hostname or "").lower() if script.url else target_host
        if not host:
            continue
        entry = _entry(host)
        if script.url and script.url not in entry["scripts"]:
            entry["scripts"].append(script.url)
    if result.meta and target_host:
        _entry(target_host)["meta"] = list(result.meta)
    return {h: v for h, v in signal.items() if any(v.values())}
```
  In `capture_run`, after `kept = _in_scope(...)` (and after the manifest commit block, alongside the existing `publish(redis, event)`), write the signal. Insert just before the final `log.info("capture.done", ...)`:
```python
    signal = _build_signal(result, target_host=target_host, kept=kept)
    if signal:
        signal_ref = storage.put_blob(
            tenant_id, run_id, "fingerprint-signal", json.dumps(signal).encode("utf-8")
        )
        with tenant_session(tenant_id) as session:
            record_event(
                session,
                tenant_id=tenant_id,
                run_id=run_id,
                event_type="fingerprint.signal",
                payload={"signal_ref": signal_ref, "hosts": len(signal)},
            )
```
- [ ] **Step 4: Run test to verify it passes** — from `apps/platform`:
  `RECON_REQUIRE_ENGINES=1 uv run pytest -m "not integration" src/recon/capture/capture_signal_test.py src/recon/capture/driver_test.py src/recon/capture/stage_test.py -v`
  Expected: new tests pass; existing capture tests still pass (the new `CaptureResult` fields are defaulted, so slice-2 fakes stay valid). `uv run ruff check src/recon/capture`.
- [ ] **Step 5: Commit** —
  `git add apps/platform/src/recon/capture/driver.py apps/platform/src/recon/capture/stage.py apps/platform/src/recon/capture/capture_signal_test.py`
```
git commit -m "feat(capture): harvest fingerprint signal (headers, meta, scriptSrc, cookies)

driver records allowlisted Network.responseReceived headers + cookie NAMES per host
and reads <meta name=generator> once after settle; stage folds them with the kept
scripts into one host-keyed fingerprint-signal blob + fingerprint.signal event, the
same schema/event the fetch path emits so analyze reads both modes identically (T1/T6).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Analyze best-effort per-host fingerprint pass
**Files:**
- Modify `apps/platform/src/recon/findings/analyze.py` (imports; restructure `analyze_run` ~lines 93-147 to call the pass; add `_fingerprint_run` + helpers)
- Test: `apps/platform/src/recon/findings/analyze_technologies_test.py`

**Interfaces:** Consumes `techdetect.detect`, `techdetect.dataset_commit`, `techdetect.skipped_pattern_count`, `models.RunTechnology`, the `fingerprint.signal` event/blob from Tasks 6-7, `run_assets.list_for_run`, `run_queries.raise_if_control_requested`, `retry.ControlInterrupt`, `record_event`/`publish`. Produces `run_technology` rows upserted on `(run_id, host, name)` (T3) and an `analyze.technologies` RunEvent (`{hosts: {host: count}, dataset_commit, skipped_patterns}`). The whole pass is swallowed (never fails the run — T2) except `ControlInterrupt`, which propagates.

- [ ] **Step 1: Write the failing test** — `apps/platform/src/recon/findings/analyze_technologies_test.py` (integration; DB + blobs):
```python
import json

import pytest
from sqlalchemy import update

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.events.log import record_event
from recon.findings import analyze
from recon.runs import service
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


def _run_with_signal(redis, tenant, session_id, signal: dict) -> str:
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    # One asset so analyze has a unit to run; its bytes double as the scripts surface.
    key = storage.put_blob(tenant, view.id, "input", b"/*react*/ console.log(1)")
    with tenant_session(tenant) as session:
        session.add(models.RunAsset(
            tenant_id=tenant, run_id=view.id, url="https://acme.io/app.js",
            input_ref=key, fetch_status="ok",
        ))
    signal_ref = storage.put_blob(
        tenant, view.id, "fingerprint-signal", json.dumps(signal).encode("utf-8")
    )
    with tenant_session(tenant) as session:
        record_event(session, tenant_id=tenant, run_id=view.id,
                     event_type="fingerprint.signal", payload={"signal_ref": signal_ref, "hosts": 1})
    return view.id


def test_fingerprint_pass_writes_run_technology(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _run_with_signal(redis, tenant, session_id, {
        "acme.io": {"headers": {"server": "nginx/1.25.3"}, "scripts": [], "meta": [], "cookies": []}
    })
    analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id)
    with tenant_session(tenant) as session:
        rows = session.query(models.RunTechnology).all()
    names = {r.name: r for r in rows}
    assert names["Nginx"].version == "1.25.3" and names["Nginx"].host == "acme.io"


def test_fingerprint_pass_is_idempotent_on_redelivery(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _run_with_signal(redis, tenant, session_id, {
        "acme.io": {"headers": {"server": "nginx"}, "scripts": [], "meta": [], "cookies": []}
    })
    analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id)
    analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id)  # redeliver
    with tenant_session(tenant) as session:
        assert session.query(models.RunTechnology).filter_by(name="Nginx").count() == 1


def test_a_fingerprint_error_never_fails_the_run(redis, authorized_session, monkeypatch):
    tenant, session_id = authorized_session
    run_id = _run_with_signal(redis, tenant, session_id, {
        "acme.io": {"headers": {"server": "nginx"}, "scripts": [], "meta": [], "cookies": []}
    })
    monkeypatch.setattr(
        "recon.findings.analyze.techdetect.detect",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    # Must NOT raise — best-effort (T2). Findings from the asset still land.
    analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id)
    with tenant_session(tenant) as session:
        assert session.query(models.RunTechnology).count() == 0
```
- [ ] **Step 2: Run test to verify it fails** — bring up infra (`docker compose up -d postgres redis minio migrate`), then:
  `RECON_S3_ENDPOINT_URL=http://localhost:9000 RECON_S3_ACCESS_KEY=recon RECON_S3_SECRET_KEY=recon-secret RECON_S3_BUCKET=recon-artifacts uv run pytest src/recon/findings/analyze_technologies_test.py -v`
  Expected: fails — no `run_technology` rows are written (the fingerprint pass doesn't exist yet); the monkeypatch target `recon.findings.analyze.techdetect` doesn't exist → `AttributeError`.
- [ ] **Step 3: Write minimal implementation.**
  In `analyze.py`, add imports: `from urllib.parse import urlsplit`, `from sqlalchemy import select`, `from sqlalchemy.dialects.postgresql import insert as pg_insert`; add `RunEvent, RunTechnology` to the `from recon.db.models import ...`; add `from recon.queue import retry`; add `techdetect` to the `from recon.findings import (...)` group. Add module constants near `_SOURCE_NAME`:
```python
_MAX_SIGNAL_BYTES = 2_000_000  # cap on the loaded signal blob (best-effort, bounded)
_MAX_JS_BYTES_PER_HOST = 2_000_000  # cap on JS fed to the scripts-field matcher, per host
```
  Restructure `analyze_run` so BOTH branches flow through the fingerprint pass before returning. Replace the body after `wrappers = _session_wrappers(...)`:
```python
    wrappers = _session_wrappers(tenant_id, run_id)  # REQ-D5: recognize taught wrappers live
    rows = run_assets.list_for_run(tenant_id, run_id)
    if rows:
        coverage = _analyze_assets(
            redis, tenant_id=tenant_id, run_id=run_id, job_id=job_id, rows=rows, wrappers=wrappers
        )
    else:
        coverage = _analyze_legacy(redis, tenant_id=tenant_id, run_id=run_id, wrappers=wrappers)
    # Best-effort per-host fingerprint pass (T2): enrichment that must NEVER fail the
    # run (a raise would DLQ -> run FAILED -> all findings lost). A cooperative
    # control interrupt is not a failure, so it propagates.
    try:
        _fingerprint_run(redis, tenant_id=tenant_id, run_id=run_id, job_id=job_id)
    except retry.ControlInterrupt:
        raise
    except Exception as exc:  # noqa: BLE001 - best-effort enrichment; log, never fail the run
        log.warning("analyze.fingerprint_failed", run_id=run_id, error=str(exc))
    return coverage
```
  Extract the current inline legacy path (old lines 116-147) into `_analyze_legacy` verbatim (same logic, returns `Coverage`):
```python
def _analyze_legacy(redis: Redis, *, tenant_id: str, run_id: str, wrappers) -> Coverage:
    """The upload/single-URL path: analyze ``run.input_ref`` as one unit (unchanged)."""
    with tenant_session(tenant_id) as session:
        run = session.get(Run, run_id)
        input_ref = run.input_ref if run is not None else None
        source_map_ref = run.source_map_ref if run is not None else None
    if not input_ref:
        return Coverage(0, 0, 0)
    with tenant_session(tenant_id) as session:  # one REQ-A3 staging transaction
        coverage, coverage_event = _analyze_blob(
            session, tenant_id=tenant_id, run_id=run_id, input_ref=input_ref,
            source_map_ref=source_map_ref, run_asset_id=None, asset_url=None, wrappers=wrappers,
        )
    publish(redis, coverage_event)
    log.info("analyze.done", run_id=run_id, attributed=coverage.attributed,
             unattributed=coverage.unattributed, secrets=coverage.secrets,
             secrets_engine=coverage.secrets_engine, sources_recovered=coverage.sources_recovered,
             source_map=coverage.source_map, findings=coverage.findings_written)
    return coverage
```
  Add the fingerprint pass + helpers (all fully typed — this file is mypy-strict):
```python
def _fingerprint_run(redis: Redis, *, tenant_id: str, run_id: str, job_id: str | None) -> None:
    """Detect per-host technologies from the run's fingerprint-signal blob and upsert
    ``run_technology`` (T3). Heartbeats + checks control between hosts (REQ-A4)."""
    signal = _load_fingerprint_signal(tenant_id, run_id)
    if not signal:
        return
    js_by_host = _js_texts_by_host(tenant_id, run_id, hosts=set(signal))
    host_counts: dict[str, int] = {}
    with tenant_session(tenant_id) as session:
        for host, host_signal in signal.items():
            run_queries.raise_if_control_requested(tenant_id, run_id)  # REQ-A4 (propagates)
            if job_id:
                progress.beat(
                    redis, tenant_id=tenant_id, run_id=run_id, job_id=job_id,
                    done=0, total=0, emit_event=False,
                )
            detections = techdetect.detect(host, host_signal, js_by_host.get(host, []))
            for detection in detections:
                _upsert_technology(session, tenant_id, run_id, host, detection)
            host_counts[host] = len(detections)
        event = record_event(
            session, tenant_id=tenant_id, run_id=run_id, event_type="analyze.technologies",
            payload={
                "hosts": host_counts,
                "dataset_commit": techdetect.dataset_commit(),
                "skipped_patterns": techdetect.skipped_pattern_count(),
            },
        )
    publish(redis, event)  # commit-then-publish (REQ-R2)
    log.info("analyze.technologies", run_id=run_id, hosts=host_counts)


def _load_fingerprint_signal(tenant_id: str, run_id: str) -> dict[str, Any]:
    """The latest ``fingerprint.signal`` blob for the run as ``{host: HostSignal}``,
    size-capped; ``{}`` if absent/oversized/invisible."""
    with tenant_session(tenant_id) as session:
        payload = session.scalar(
            select(RunEvent.payload)
            .where(RunEvent.run_id == str(run_id), RunEvent.type == "fingerprint.signal")
            .order_by(RunEvent.id.desc())
        )
    if not payload:
        return {}
    ref = payload.get("signal_ref")
    if not ref:
        return {}
    raw = storage.get_blob(ref)
    if len(raw) > _MAX_SIGNAL_BYTES:
        log.warning("analyze.fingerprint_signal_oversized", run_id=run_id, bytes=len(raw))
        return {}
    data = json.loads(raw.decode("utf-8"))
    return data if isinstance(data, dict) else {}


def _js_texts_by_host(tenant_id: str, run_id: str, *, hosts: set[str]) -> dict[str, list[str]]:
    """Stored JS bytes per host for the ``scripts``-field match, capped per host."""
    by_host: dict[str, list[str]] = {}
    budget: dict[str, int] = {}
    for asset in run_assets.list_for_run(tenant_id, run_id):
        if not asset.input_ref:
            continue
        host = (urlsplit(asset.url).hostname or "").lower()
        if host not in hosts or budget.get(host, 0) >= _MAX_JS_BYTES_PER_HOST:
            continue
        raw = storage.get_blob(asset.input_ref)
        budget[host] = budget.get(host, 0) + len(raw)
        by_host.setdefault(host, []).append(raw[:_MAX_JS_BYTES_PER_HOST].decode("utf-8", "replace"))
    return by_host


def _upsert_technology(
    session: Session, tenant_id: str, run_id: str, host: str, detection: techdetect.Detection
) -> None:
    """Upsert one detection on ``(run_id, host, name)`` — redelivery-safe (T3)."""
    stmt = (
        pg_insert(RunTechnology)
        .values(
            tenant_id=str(tenant_id), run_id=str(run_id), host=host, name=detection.name,
            categories=detection.categories, version=detection.version,
            confidence=detection.confidence, evidence=detection.evidence,
        )
        .on_conflict_do_update(
            index_elements=["run_id", "host", "name"],
            set_={
                "categories": detection.categories, "version": detection.version,
                "confidence": detection.confidence, "evidence": detection.evidence,
            },
        )
    )
    session.execute(stmt)
```
- [ ] **Step 4: Run test to verify it passes** —
  `RECON_S3_ENDPOINT_URL=http://localhost:9000 RECON_S3_ACCESS_KEY=recon RECON_S3_SECRET_KEY=recon-secret RECON_S3_BUCKET=recon-artifacts uv run pytest src/recon/findings/analyze_technologies_test.py src/recon/findings/analyze_multi_test.py -v`
  Expected: 3 new tests pass; existing analyze tests still pass. Then `uv run mypy src/recon/findings/analyze.py` — no errors — and `uv run ruff check src/recon/findings/analyze.py`.
- [ ] **Step 5: Commit** —
  `git add apps/platform/src/recon/findings/analyze.py apps/platform/src/recon/findings/analyze_technologies_test.py`
```
git commit -m "feat(analyze): best-effort per-host tech-detection fingerprint pass

After per-asset analysis, load the run's fingerprint-signal blob and, per host,
detect technologies (size-capped) and upsert run_technology on (run_id, host, name)
(T3), emitting an analyze.technologies event (commit-then-publish). The whole pass is
swallowed and logged so it can never fail the run (T2); ControlInterrupt propagates
and hosts are heartbeated + interruptible (REQ-A4).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: API — `queries.list_technologies` + `tech_router` + registration
**Files:**
- Modify `apps/platform/src/recon/findings/queries.py` (add `TechnologyView`, `TechnologiesView`, `list_technologies`)
- Create `apps/platform/src/recon/api/tech_router.py`
- Modify `apps/platform/src/recon/api/app.py` (import + `include_router` ~lines 17-29, 53-63)
- Test: `apps/platform/src/recon/api/tech_router_test.py`

**Interfaces:** Consumes `models.RunTechnology`, `models.Run`, `tenant_session`, `get_tenant_id`. Produces `queries.TechnologyView(name, categories, version, confidence, evidence)`, `queries.TechnologiesView(run_id: str, hosts: dict[str, list[TechnologyView]])`, `queries.list_technologies(tenant_id: str, run_id: str) -> TechnologiesView | None` (None → RLS-invisible/unknown run → 404), and `GET /runs/{run_id}/technologies` returning `{run_id, count, hosts: {host: [{name, categories, version, confidence, evidence}]}}`.

- [ ] **Step 1: Write the failing test** — `apps/platform/src/recon/api/tech_router_test.py`:
```python
import pytest
from fastapi.testclient import TestClient

from recon.api.app import create_app
from recon.db import models
from recon.db.base import tenant_session
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


@pytest.fixture()
def client():
    return TestClient(create_app())


def _headers(tenant):
    return {"X-Tenant-Id": tenant}


def _run_with_tech(tenant, session_id) -> str:
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        session.add(models.RunTechnology(
            tenant_id=tenant, run_id=run_id, host="acme.io", name="Nginx",
            categories=["Web servers"], version="1.25.3", confidence=100,
            evidence=["server: nginx/1.25.3"],
        ))
        return run_id


def test_get_technologies_groups_by_host(client, authorized_session):
    tenant, session_id = authorized_session
    run_id = _run_with_tech(tenant, session_id)
    resp = client.get(f"/runs/{run_id}/technologies", headers=_headers(tenant))
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == run_id and body["count"] == 1
    tech = body["hosts"]["acme.io"][0]
    assert tech["name"] == "Nginx" and tech["version"] == "1.25.3"
    assert tech["categories"] == ["Web servers"] and tech["confidence"] == 100


def test_unknown_run_is_404(client, tenant):
    resp = client.get(
        "/runs/00000000-0000-0000-0000-000000000000/technologies", headers=_headers(tenant)
    )
    assert resp.status_code == 404


def test_other_tenant_sees_none(client, authorized_session):
    tenant, session_id = authorized_session
    run_id = _run_with_tech(tenant, session_id)
    other = sessions_service.create_tenant("tech-other")
    resp = client.get(f"/runs/{run_id}/technologies", headers=_headers(other))
    assert resp.status_code == 404  # RLS -> run invisible -> None -> 404
```
- [ ] **Step 2: Run test to verify it fails** — bring up infra, then:
  `RECON_S3_ENDPOINT_URL=http://localhost:9000 RECON_S3_ACCESS_KEY=recon RECON_S3_SECRET_KEY=recon-secret RECON_S3_BUCKET=recon-artifacts uv run pytest src/recon/api/tech_router_test.py -v`
  Expected: 404 for the happy path (route not registered) — all three tests fail.
- [ ] **Step 3: Write minimal implementation.**
  In `findings/queries.py`, add `RunTechnology` to the `from recon.db.models import (...)` block, add the views (near the other dataclasses), and the query (after `list_findings`):
```python
@dataclass(frozen=True)
class TechnologyView:
    name: str
    categories: list[str]
    version: str | None
    confidence: int
    evidence: list[str]


@dataclass(frozen=True)
class TechnologiesView:
    run_id: str
    hosts: dict[str, list[TechnologyView]]


def list_technologies(tenant_id: str, run_id: str) -> TechnologiesView | None:
    """Every detected technology for a run, grouped by host, or ``None`` if the run
    does not exist for this tenant (RLS-invisible). Deterministic order."""
    with tenant_session(tenant_id) as session:
        run = session.get(Run, run_id)
        if run is None:
            return None
        rows = session.scalars(
            select(RunTechnology)
            .where(RunTechnology.run_id == str(run_id))
            .order_by(RunTechnology.host, RunTechnology.confidence.desc(), RunTechnology.name)
        ).all()
    hosts: dict[str, list[TechnologyView]] = {}
    for row in rows:
        hosts.setdefault(row.host, []).append(
            TechnologyView(
                name=row.name,
                categories=list(row.categories or []),
                version=row.version,
                confidence=row.confidence,
                evidence=list(row.evidence or []),
            )
        )
    return TechnologiesView(run_id=str(run_id), hosts=hosts)
```
  Create `api/tech_router.py` (mirrors `findings_router.py`):
```python
"""Technologies read endpoint: ``GET /runs/{run_id}/technologies`` (tech detection).

A thin read over the per-host technology stack the analyze fingerprint pass produced.
Isolation is the database's (RLS): a run absent for this tenant is a 404, distinct
from a run with zero detected technologies (200 + empty ``hosts``)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from recon.api.deps import get_tenant_id
from recon.findings import queries

router = APIRouter(tags=["technologies"])


@router.get("/runs/{run_id}/technologies")
def get_run_technologies(
    run_id: str,
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    result = queries.list_technologies(tenant_id, run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="run not found")
    return {
        "run_id": result.run_id,
        "count": sum(len(techs) for techs in result.hosts.values()),
        "hosts": {
            host: [
                {
                    "name": tech.name,
                    "categories": tech.categories,
                    "version": tech.version,
                    "confidence": tech.confidence,
                    "evidence": tech.evidence,
                }
                for tech in techs
            ]
            for host, techs in result.hosts.items()
        },
    }
```
  In `api/app.py`, add `tech_router` to the `from recon.api import (...)` import block and register it after `findings_router`:
```python
    app.include_router(findings_router.router)
    app.include_router(tech_router.router)
```
  Note: no SPA-routing change is needed — `/runs/{id}/technologies` (two segments) is served by the API for `Accept: application/json` fetches, exactly like `/runs/{id}/findings` (the `spa_navigation` middleware + `run_subpage` regex already handle the text/html vs. json split).
- [ ] **Step 4: Run test to verify it passes** —
  `RECON_S3_ENDPOINT_URL=http://localhost:9000 RECON_S3_ACCESS_KEY=recon RECON_S3_SECRET_KEY=recon-secret RECON_S3_BUCKET=recon-artifacts uv run pytest src/recon/api/tech_router_test.py -v`
  Expected: 3 passed. `uv run mypy src/recon/findings/queries.py` (strict — must be clean) and `uv run ruff check src/recon/findings/queries.py src/recon/api/tech_router.py src/recon/api/app.py`.
- [ ] **Step 5: Commit** —
  `git add apps/platform/src/recon/findings/queries.py apps/platform/src/recon/api/tech_router.py apps/platform/src/recon/api/app.py apps/platform/src/recon/api/tech_router_test.py`
```
git commit -m "feat(api): GET /runs/{id}/technologies per-host tech stack

queries.list_technologies reads run_technology under tenant_session (RLS -> 404 on
an invisible run), grouped by host; a thin tech_router mirrors findings_router and is
registered in app.py. 200 + empty hosts is distinct from a 404 unknown run.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 10: Web — apiClient + types + RunData + Overview card + Tech page + nav/route
**Files:**
- Modify `apps/platform/web/src/api/types.ts` (add `Technology`, `TechnologiesResponse`)
- Modify `apps/platform/web/src/api/apiClient.ts` (add `getTechnologies`; import the new types)
- Modify `apps/platform/web/src/features/progress/runData.tsx` (fold `technologies` into `RunData`)
- Modify `apps/platform/web/src/features/overview/OverviewPanel.tsx` (a "Tech stack" card)
- Create `apps/platform/web/src/features/tech/TechPage.tsx`
- Create `apps/platform/web/src/features/tech/TechPage.test.tsx`
- Modify `apps/platform/web/src/app.tsx` (add `TechRoute`)
- Modify `apps/platform/web/src/main.tsx` (add the `tech` child route)
- Modify `apps/platform/web/src/shell/Sidebar.tsx` (add the nav item)
- Modify `apps/platform/web/src/features/overview/OverviewPanel.test.tsx` (assert the new card)

**Interfaces:** Consumes `GET /runs/{id}/technologies`. Produces `types.Technology { name; categories: string[]; version: string | null; confidence: number; evidence: string[] }`, `types.TechnologiesResponse { run_id; count; hosts: Record<string, Technology[]> }`, `apiClient.getTechnologies(tenantId, runId) => Promise<TechnologiesResponse>`, and `RunData.technologies: TechnologiesResponse | null`.

- [ ] **Step 1: Write the failing test** — `apps/platform/web/src/features/tech/TechPage.test.tsx`:
```tsx
import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { TechPage } from "./TechPage";
import type { TechnologiesResponse } from "../../api/types";

const data: TechnologiesResponse = {
  run_id: "r1", count: 2,
  hosts: {
    "acme.io": [
      { name: "Nginx", categories: ["Web servers"], version: "1.25.3", confidence: 100, evidence: ["server: nginx/1.25.3"] },
      { name: "jQuery", categories: ["JavaScript libraries"], version: "3.5.1", confidence: 100, evidence: ["scriptSrc: jquery-3.5.1.min.js"] },
    ],
  },
};

describe("TechPage", () => {
  it("renders per-host technologies with version, category and confidence", () => {
    render(<TechPage data={data} />);
    expect(screen.getByText("acme.io")).toBeInTheDocument();
    const nginx = screen.getByText("Nginx").closest("tr") as HTMLElement;
    expect(within(nginx).getByText("1.25.3")).toBeInTheDocument();
    expect(within(nginx).getByText("Web servers")).toBeInTheDocument();
    expect(within(nginx).getByText("100")).toBeInTheDocument();
    expect(screen.getByText("jQuery")).toBeInTheDocument();
  });

  it("shows an empty state when nothing was detected", () => {
    render(<TechPage data={{ run_id: "r1", count: 0, hosts: {} }} />);
    expect(screen.getByText(/no technologies/i)).toBeInTheDocument();
  });
});
```
  And extend `OverviewPanel.test.tsx` with a Tech-stack-card case (append inside the `describe`):
```tsx
  it("shows a Tech stack card counting technologies across hosts", () => {
    const data: FindingsResponse = {
      run_id: "r", count: 0, coverage: null, spec: null, findings: [],
    };
    const technologies = {
      run_id: "r", count: 3,
      hosts: { "acme.io": [
        { name: "Nginx", categories: ["Web servers"], version: "1.25.3", confidence: 100, evidence: [] },
        { name: "jQuery", categories: ["JavaScript libraries"], version: "3.5.1", confidence: 100, evidence: [] },
        { name: "React", categories: ["JavaScript frameworks"], version: null, confidence: 100, evidence: [] },
      ] },
    };
    const router = createMemoryRouter(
      [{ path: "/runs/:id", element: <OverviewPanel data={data} technologies={technologies} /> }],
      { initialEntries: ["/runs/r1"] },
    );
    render(<RouterProvider router={router} />);
    expect(within(card("Tech stack")).getByText("3")).toBeInTheDocument();
  });
```
- [ ] **Step 2: Run test to verify it fails** — from `apps/platform/web`:
  `npm test -- TechPage OverviewPanel`
  Expected: `TechPage` import fails (module missing); the new OverviewPanel case fails (`Tech stack` card not found; `technologies` prop unknown).
- [ ] **Step 3: Write minimal implementation.**
  In `types.ts`, append:
```ts
// Tech detection: one detected technology (recon.findings.queries.TechnologyView).
// `version` is null when not statically derivable (Phase 1 honesty — T12).
export interface Technology {
  name: string; categories: string[]; version: string | null; confidence: number; evidence: string[];
}
// GET /runs/{id}/technologies — per-host stack. `hosts` empty (200) is distinct
// from a 404 unknown run.
export interface TechnologiesResponse {
  run_id: string; count: number; hosts: Record<string, Technology[]>;
}
```
  In `apiClient.ts`, add `TechnologiesResponse` to the type import list and add the call (next to `getFindings`):
```ts
export function getTechnologies(tenantId: string, runId: string): Promise<TechnologiesResponse> {
  return request(`/runs/${encodeURIComponent(runId)}/technologies`, {}, tenantId);
}
```
  In `runData.tsx`: import `getTechnologies` and `type TechnologiesResponse`; add `technologies: TechnologiesResponse | null;` to the `RunData` interface; add the state `const [technologies, setTechnologies] = useState<TechnologiesResponse | null>(null);`; fetch it in `refresh()` — extend the `Promise.all` to `getTechnologies` and set it (best-effort like the manifest so it never breaks the panel):
```ts
        const [s, f] = await Promise.all([getStatus(tenantId, runId), getFindings(tenantId, runId)]);
        // ...existing status/findings handling...
        try {
          const techs = await getTechnologies(tenantId, runId);
          if (!controller.signal.aborted) setTechnologies(techs);
        } catch { /* ignore — technologies are best-effort enrichment */ }
```
  and add `technologies` to the returned object:
```ts
  return {
    runId, sessionId, state, stage, pct, done, total, eta, error, assets, events, findings, loaded,
    pauseRequested, cancelRequested, captureStatus, technologies, handleControlResult,
  };
```
  In `OverviewPanel.tsx`: accept an optional prop and add a 5th metric card. Change the signature and add the card:
```tsx
import type { FindingsResponse, Finding, TechnologiesResponse } from "../../api/types";
// ...
export function OverviewPanel({ data, technologies }: { data: FindingsResponse; technologies?: TechnologiesResponse | null }) {
  // ...existing derivations...
  const techCount = technologies ? technologies.count : null;
  const techTop = technologies
    ? Object.values(technologies.hosts).flat().slice(0, 3).map((t) => t.name).join(", ")
    : null;
```
  and append to the `metrics` array:
```tsx
    { key: "tech", label: "Tech stack", section: "tech",
      value: techCount == null ? DASH : String(techCount),
      sub: techTop || "server · framework · libs" },
```
  Wire the prop at the call site in `app.tsx` (`OverviewRoute`): `const { findings, technologies, state } = useRunData();` and `{findings && <OverviewPanel data={findings} technologies={technologies} />}`.
  Create `features/tech/TechPage.tsx`:
```tsx
import type { TechnologiesResponse } from "../../api/types";

// The per-host technology stack — the threat-model-grade surface (name · category ·
// version · confidence · evidence). Version is "—" when not statically derivable
// (Phase 1 honesty — T12).
export function TechPage({ data }: { data: TechnologiesResponse }) {
  const hosts = Object.entries(data.hosts);
  if (data.count === 0 || hosts.length === 0) {
    return (
      <div className="card">
        <h2 className="rp-title">Tech stack</h2>
        <p className="muted">No technologies detected for this run.</p>
      </div>
    );
  }
  return (
    <div className="card">
      <h2 className="rp-title">Tech stack</h2>
      {hosts.map(([host, techs]) => (
        <section key={host} className="tech-host">
          <h3 className="tech-host-name">{host}</h3>
          <table className="tech-table">
            <thead>
              <tr><th>Technology</th><th>Category</th><th>Version</th><th>Confidence</th><th>Evidence</th></tr>
            </thead>
            <tbody>
              {techs.map((t) => (
                <tr key={t.name}>
                  <td>{t.name}</td>
                  <td>{t.categories.join(", ") || "—"}</td>
                  <td>{t.version ?? "—"}</td>
                  <td>{t.confidence}</td>
                  <td className="tech-evidence">{t.evidence.join("; ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </div>
  );
}
```
  In `app.tsx`, import and add the route component (mirror `FindingsRoute`'s loaded/empty gating):
```tsx
import { TechPage } from "./features/tech/TechPage";
// ...
export function TechRoute() {
  const { technologies, loaded } = useRunData();
  if (!loaded) return <NotReady title="Loading…" body="Fetching this run's technologies." />;
  if (!technologies) return <NotReady title="No tech stack yet" body="Technologies appear here once analysis has run." />;
  return <TechPage data={technologies} />;
}
```
  In `main.tsx`, import `TechRoute` from `./app` and add the child route (after `probe`):
```tsx
      { path: "tech", Component: TechRoute },
```
  In `Sidebar.tsx`, add the nav item to `NAV_ITEMS` (after `probe`, before `threat-model`):
```tsx
  { id: "tech", label: "Tech stack", icon: "layers" },
```
- [ ] **Step 4: Run test to verify it passes** — from `apps/platform/web`:
  `npm test -- TechPage OverviewPanel` (both green), then `npm run lint` (oxlint + tsc) and `npm run build`.
  Expected: TechPage 2 tests pass; OverviewPanel suite (incl. the new card case) passes; lint + build clean.
- [ ] **Step 5: Commit** —
  `git add apps/platform/web/src/api/types.ts apps/platform/web/src/api/apiClient.ts apps/platform/web/src/features/progress/runData.tsx apps/platform/web/src/features/overview/OverviewPanel.tsx apps/platform/web/src/features/overview/OverviewPanel.test.tsx apps/platform/web/src/features/tech apps/platform/web/src/app.tsx apps/platform/web/src/main.tsx apps/platform/web/src/shell/Sidebar.tsx`
```
git commit -m "feat(web): Tech stack overview card + per-host Tech page

Adds getTechnologies + Technology/TechnologiesResponse types, folds technologies into
RunData (best-effort, shared by every run page), a Tech stack overview metric card,
and a /runs/:id/tech page with a per-host name/category/version/confidence/evidence
table + empty state. Version renders "—" when not statically derivable (T12).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 11: Licensing + packaging + CI guard (GPL server-side enforcement)
**Files:**
- Create `LICENSE` (repo root)
- Create `NOTICE` (repo root)
- Modify `apps/platform/pyproject.toml` (`[tool.setuptools.package-data]` ~lines 77-81)
- Modify `.github/workflows/ci.yml` (add a guard step to the `host-tests` job ~after line 55)
- Test: `apps/platform/src/recon/findings/techdetect/packaging_test.py`

**Interfaces:** Consumes `dataset.load_raw` (proves package-data resolves). Produces the root `LICENSE`/`NOTICE`, the `techdetect_data` package-data declaration, and a CI step that fails if `apps/capture/` references `techdetect_data` (T10).

- [ ] **Step 1: Write the failing test** — `apps/platform/src/recon/findings/techdetect/packaging_test.py`:
```python
from pathlib import Path

import tomllib

from recon.findings.techdetect import dataset


def test_dataset_resolves_via_package_data():
    # importlib.resources must find the vendored JSON — the wheel-drops-data class of
    # bug (the Kingfisher AKIA rule) only bites once package-data is declared.
    techs, categories, commit = dataset.load_raw()
    assert techs and categories and commit


def test_techdetect_data_is_declared_package_data():
    root = Path(__file__).resolve().parents[6]  # repo root
    pyproject = tomllib.loads((root / "apps/platform/pyproject.toml").read_text(encoding="utf-8"))
    package_data = pyproject["tool"]["setuptools"]["package-data"]
    assert "recon.findings.techdetect_data" in package_data
    assert any(g.endswith("*.json") for g in package_data["recon.findings.techdetect_data"])


def test_gpl_notice_names_the_enthec_dataset_server_side_only():
    root = Path(__file__).resolve().parents[6]
    notice = (root / "NOTICE").read_text(encoding="utf-8")
    assert "enthec" in notice and "GPL-3.0" in notice and "server-side" in notice.lower()
```
- [ ] **Step 2: Run test to verify it fails** — from `apps/platform`:
  `RECON_REQUIRE_ENGINES=1 uv run pytest -m "not integration" src/recon/findings/techdetect/packaging_test.py -v`
  Expected: `test_techdetect_data_is_declared_package_data` fails (key absent) and `test_gpl_notice_...` fails (`FileNotFoundError`/no NOTICE). `test_dataset_resolves_via_package_data` may already pass in the source tree (importlib.resources reads the tree in dev), but the declaration + notice tests fail.
- [ ] **Step 3: Write minimal implementation.**
  In `pyproject.toml`, extend `[tool.setuptools.package-data]`:
```toml
[tool.setuptools.package-data]
"recon.findings" = ["rules/*.yml"]
# Vendored enthec/webappanalyzer fingerprint dataset (GPL-3.0, server-side only —
# see root NOTICE). Declared package-data or the wheel drops the JSON and every tech
# detection fails to load (the Kingfisher AKIA-rule wheel-drop class of bug).
"recon.findings.techdetect_data" = ["*.json", "commit.txt"]
```
  Create root `LICENSE` — the platform's own license header plus a pointer to `NOTICE` for third-party terms (use the project's chosen license text; the load-bearing content for this slice is the NOTICE). Minimal `LICENSE`:
```
Copyright (c) 2026 recon-platform authors.

This repository's first-party source is proprietary/internal. Third-party
components and their licenses — including the GPL-3.0 enthec/webappanalyzer
fingerprint dataset used server-side only — are recorded in NOTICE.
```
  Create root `NOTICE`:
```
recon-platform — third-party notices
====================================

Technology fingerprint dataset
------------------------------
apps/platform/src/recon/findings/techdetect_data/ vendors a subset of
enthec/webappanalyzer (https://github.com/enthec/webappanalyzer), licensed under
GPL-3.0.

Usage terms (delivery model: SaaS / internal only):
- The dataset is used SERVER-SIDE ONLY. It is never conveyed to users and never
  bundled into the distributed browser extension (apps/capture/). Because it is
  not distributed, GPL-3.0 copyleft is not triggered for the platform's own code.
- A CI guard (.github/workflows/ci.yml) fails the build if apps/capture/ imports
  or references techdetect_data, making "server-side only" enforceable.
- Re-confirm licensing before ANY change to the delivery model (e.g. shipping the
  dataset to a client or bundling it into a conveyed artifact).

The pinned upstream commit is recorded in techdetect_data/commit.txt and re-pinned
via recon.findings.techdetect.refresh.
```
  In `.github/workflows/ci.yml`, add a guard step to the `host-tests` job (after the "Unit + engine contract tests" step). Use a repo-root-relative grep (the job's `working-directory` is `apps/platform`, so step up):
```yaml
      - name: Guard — enthec dataset stays server-side (GPL-3.0, T10)
        working-directory: ${{ github.workspace }}
        run: |
          if grep -rInq "techdetect_data" apps/capture; then
            echo "::error::apps/capture references techdetect_data — the GPL-3.0 enthec dataset must stay server-side (see NOTICE)"
            grep -rIn "techdetect_data" apps/capture
            exit 1
          fi
          echo "ok: apps/capture does not reference techdetect_data"
```
- [ ] **Step 4: Run test to verify it passes** — from `apps/platform`:
  `RECON_REQUIRE_ENGINES=1 uv run pytest -m "not integration" src/recon/findings/techdetect/packaging_test.py -v`
  Expected: 3 passed. Manually dry-run the guard from the repo root: `grep -rIn "techdetect_data" apps/capture || echo "clean"` → prints `clean`. Then run the full techdetect suite once more: `RECON_REQUIRE_ENGINES=1 uv run pytest -m "not integration" src/recon/findings/techdetect -v` (all green).
- [ ] **Step 5: Commit** —
  `git add LICENSE NOTICE apps/platform/pyproject.toml .github/workflows/ci.yml apps/platform/src/recon/findings/techdetect/packaging_test.py`
```
git commit -m "chore(licensing): GPL NOTICE + techdetect_data package-data + CI server-side guard

Root LICENSE/NOTICE record the enthec/webappanalyzer dataset (GPL-3.0, server-side
only, never bundled into the extension). Declares techdetect_data as package-data so
the wheel ships the JSON (the Kingfisher wheel-drop class of bug). A CI step fails the
build if apps/capture references techdetect_data, making 'server-side only' enforceable
(T10).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification (run before opening the PR)

From `apps/platform` (host lane, mirrors CI exactly):
```
uv sync --frozen --extra dev
uv run ruff check src && uv run ruff format --check src
uv run mypy src/recon/findings src/recon/spec        # strict — techdetect + analyze + queries clean
RECON_REQUIRE_ENGINES=1 uv run pytest -m "not integration" --cov=recon --cov-fail-under=60
```
From `apps/platform/web`:
```
npm ci && npm run lint && npm test && npm run build
```
Integration lane (needs live stores — `docker compose up -d postgres redis minio migrate`):
```
RECON_S3_ENDPOINT_URL=http://localhost:9000 RECON_S3_ACCESS_KEY=recon RECON_S3_SECRET_KEY=recon-secret RECON_S3_BUCKET=recon-artifacts \
  uv run pytest src/recon/db/run_technology_model_test.py src/recon/findings/analyze_technologies_test.py src/recon/api/tech_router_test.py -v
```
Then both §4 review gates on the diff: the adversarial design review is already recorded in the spec; run a higher-model code review of the implemented diff before merge.

## Acceptance (from the spec, mapped to tasks)
- Component A (signal harvest): Tasks 6 (fetch) + 7 (capture).
- Component B (engine package): Tasks 2 (version) + 3 (compile) + 4 (dataset) + 5 (match/detect).
- Component C (analyze integration): Task 8.
- Component D (model + migration): Task 1.
- Component E (API): Task 9.
- Component F (web UI): Task 10.
- Component G (licensing/CI): Task 11.
- Traps: T1 (Tasks 6/7 allowlist tests + evidence bound in 5), T2 (Task 8 best-effort test), T3 (Tasks 5 confidence-sum + 8 upsert idempotency), T4 (Tasks 3/4 skip+count), T5 (Task 6 fetch_url unchanged), T6 (Tasks 6/7 one blob per run), T7 (Task 4 fail-closed load + load-time test), T8 (Task 3 typed adapter + Task 4 TypedDict; strict mypy in the final gate), T9 (techdetect is a package of <300-line modules), T10 (Task 11), T11 (host from asset URLs in Tasks 6/7/8), T12 (version honesty — null version rendered "—" in Task 10).
