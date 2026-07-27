# Slice Y — multi-asset analyze — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the Slice X katana assets manifest into fetch/analyze so every discovered `.js` asset becomes findings, with per-asset attribution, secret reveal, and honest `DONE`/`PARTIAL` completeness.

**Architecture:** A new `run_asset` table (one row per discovered asset: blob ref + per-asset fetch/analyze status) is the asset dimension; the occurrence links to it (and the asset enters occurrence identity, `finding_hash` untouched, so a finding dedups across assets into N occurrences). Discover seeds the rows; fetch and analyze each loop them in one heartbeating, best-effort, cooperatively-interruptible stage; the coordinator computes completeness from the rows.

**Tech Stack:** Python 3.12, SQLAlchemy 2 + Postgres (RLS), Alembic, Redis Streams, pytest; React + Vite + Vitest (web).

## Global Constraints

- Test runner (host lane, no engines): `./.venv/Scripts/python.exe -m pytest -m "not integration"`.
- Integration tests are `@pytest.mark.integration`, need the Docker stack up + `RECON_REQUIRE_ENGINES=1` for real-engine cases; they apply migrations via the session `migrated` fixture (`src/recon/conftest.py`).
- Front-end: `cd web && npm test` (Vitest — does NOT type-check) and `npm run lint` (`tsc -b --noEmit`).
- Colocated tests: a source file `x.py` has its test at `x_test.py` in the same folder.
- `finding_hash` MUST NOT change (REQ-D3 identity; triage + reveal depend on it). Only `occurrence_hash` gains the asset dimension.
- Migrations: a new **table** may be built via `Base.metadata.create_all` + RLS loop (mirror `0004`); an incremental **column** add on an existing table MUST use `ADD COLUMN IF NOT EXISTS` (the `0003` remedy — see `docs/slice2-deferred-debt.md` "Migration strategy"), else a fresh-DB `create_all` makes `DuplicateColumn`.
- Conventional Commits, multi-line; end every commit body with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Keep placeholder secrets format-broken (GitHub push protection).
- Branch: `slice-y-multi-asset` (already created; the spec is already committed there).

---

### Task 1: `run_asset` table + `AssetStatus` enum + `occurrence.run_asset_id` (migration 0005)

**Files:**
- Modify: `src/recon/domain.py` (add `AssetStatus`)
- Modify: `src/recon/db/models.py` (add `RunAsset`, `FindingOccurrence.run_asset_id`, `ASSET_TABLES`)
- Create: `src/recon/migrations/versions/0005_run_asset.py`
- Create: `src/recon/db/run_asset_model_test.py`

**Interfaces:**
- Produces: `domain.AssetStatus` (`PENDING="pending"`, `OK="ok"`, `FAILED="failed"`); `models.RunAsset` with columns `id, tenant_id, run_id, url, input_ref, fetch_status, fetch_error, analyze_status, analyze_error, created_at`, `UniqueConstraint("run_id","url")`; `models.FindingOccurrence.run_asset_id: uuid|None`; `models.ASSET_TABLES = ("run_asset",)`.

- [ ] **Step 1: Add the `AssetStatus` enum**

In `src/recon/domain.py`, after `FindingType`:

```python
class AssetStatus(StrEnum):
    """Per-asset fetch/analyze outcome on a run_asset row (Slice Y)."""

    PENDING = "pending"
    OK = "ok"
    FAILED = "failed"
```

- [ ] **Step 2: Add the `RunAsset` model + occurrence column + `ASSET_TABLES`**

In `src/recon/db/models.py`, update the domain import to include `AssetStatus`:

```python
from recon.domain import AssetStatus, FindingType, JobState, QueueName, RunStage, RunState
```

Add `run_asset_id` to `FindingOccurrence` (right after the `finding_id` column):

```python
    # Slice Y: which discovered asset this sighting came from. NULL for legacy
    # single-asset (upload / single-URL) runs. Part of occurrence identity via
    # asset_url (see recon.findings.store); the row keeps the FK for reveal routing.
    run_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("run_asset.id", ondelete="SET NULL")
    )
```

Add the `RunAsset` class (after `FindingOccurrence`):

```python
class RunAsset(Base):
    """One discovered in-scope .js asset of a crawl run (Slice Y, REQ-C1/D5).

    Seeded pending by discover; fetch sets ``input_ref`` + ``fetch_status``; analyze
    sets ``analyze_status``. The per-asset blob lives at ``input_ref`` (kind="input").
    Absent for legacy single-asset runs, which keep using ``run.input_ref``."""

    __tablename__ = "run_asset"
    __table_args__ = (
        UniqueConstraint("run_id", "url", name="uq_run_asset_run_url"),
        CheckConstraint(_enum_check("fetch_status", AssetStatus), name="ck_run_asset_fetch_status"),
        CheckConstraint(_enum_check("analyze_status", AssetStatus), name="ck_run_asset_analyze_status"),
        Index("ix_run_asset_run", "tenant_id", "run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("run.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    input_ref: Mapped[str | None] = mapped_column(Text)
    fetch_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=AssetStatus.PENDING.value
    )
    fetch_error: Mapped[str | None] = mapped_column(Text)
    analyze_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=AssetStatus.PENDING.value
    )
    analyze_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _now_col(nullable=False)
```

At the bottom, after `TRIAGE_TABLES`:

```python
# Slice-Y addition, RLS-enabled by migration 0005.
ASSET_TABLES: tuple[str, ...] = ("run_asset",)
```

- [ ] **Step 3: Write the migration**

Create `src/recon/migrations/versions/0005_run_asset.py`:

```python
"""slice-Y run_asset table + finding_occurrence.run_asset_id + RLS

Revision ID: 0005_run_asset
Revises: 0004_finding_triage
Create Date: 2026-07-26

The run_asset TABLE is built from live metadata (create_all is idempotent — only
what's missing) then given FORCE RLS + the tenant_isolation policy + GRANT, exactly
like 0004. The finding_occurrence.run_asset_id COLUMN is an *incremental add on an
existing table*, so it MUST use ADD COLUMN IF NOT EXISTS — on a fresh DB 0001's
create_all already made it (the 0003 DuplicateColumn hazard); on an older dev DB the
guard adds it. The FK is enforced on fresh DBs via create_all (consistent with the
documented create_all-vs-incremental posture in slice2-deferred-debt.md).
"""

from __future__ import annotations

from alembic import op

from recon.db import models
from recon.db.base import Base

revision = "0005_run_asset"
down_revision = "0004_finding_triage"
branch_labels = None
depends_on = None

APP_ROLE = "recon_app"


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind)  # idempotent: builds run_asset (+ any missing)
    # Incremental column add on an existing table — guard against the fresh-DB
    # create_all having already made it (the 0003 bug).
    op.execute(
        'ALTER TABLE "finding_occurrence" ADD COLUMN IF NOT EXISTS run_asset_id uuid'
    )
    for table in models.ASSET_TABLES:
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
    for table in models.ASSET_TABLES:
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"')
    op.execute('ALTER TABLE "finding_occurrence" DROP COLUMN IF EXISTS run_asset_id')
    op.drop_table("run_asset")
```

- [ ] **Step 4: Write the failing RLS + column test**

Create `src/recon/db/run_asset_model_test.py`:

```python
import pytest

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


def test_run_asset_is_tenant_isolated_by_rls():
    tenant_a = sessions_service.create_tenant("asset-a")
    tenant_b = sessions_service.create_tenant("asset-b")
    sv = sessions_service.create_session(
        tenant_a, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    run_id = _make_run(tenant_a, sv.id)
    with tenant_session(tenant_a) as session:
        session.add(models.RunAsset(
            tenant_id=tenant_a, run_id=run_id, url="https://acme.io/app.js",
        ))
    with tenant_session(tenant_a) as session:
        row = session.query(models.RunAsset).one()
        assert row.fetch_status == "pending" and row.analyze_status == "pending"
    with tenant_session(tenant_b) as session:
        assert session.query(models.RunAsset).count() == 0


def test_occurrence_has_run_asset_id_column():
    # The Slice Y column exists and defaults NULL (legacy occurrences are unaffected).
    tenant = sessions_service.create_tenant("occ-col")
    sv = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    run_id = _make_run(tenant, sv.id)
    with tenant_session(tenant) as session:
        finding = models.Finding(
            tenant_id=tenant, run_id=run_id, finding_hash="a" * 64,
            type="endpoint", value="GET /x", path="input.js",
        )
        session.add(finding)
        session.flush()
        occ = models.FindingOccurrence(
            tenant_id=tenant, finding_id=finding.id, occurrence_hash="b" * 64,
        )
        session.add(occ)
        session.flush()
        assert occ.run_asset_id is None
```

- [ ] **Step 5: Run the test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/db/run_asset_model_test.py -v`
Expected: FAIL — `run_asset` table / `run_asset_id` column absent until the migration applies (the `migrated` fixture runs `alembic upgrade head`). If the dev `pgdata` volume predates the change, recreate it: `docker compose down -v && docker compose up -d postgres redis minio`.

- [ ] **Step 6: Run the test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/db/run_asset_model_test.py -v`
Expected: PASS (2 tests). Also confirm a from-scratch migrate has no `DuplicateColumn`: `docker compose run --rm api alembic upgrade head` exits 0.

- [ ] **Step 7: Commit**

```bash
git add src/recon/domain.py src/recon/db/models.py src/recon/migrations/versions/0005_run_asset.py src/recon/db/run_asset_model_test.py
git commit -m "feat(slice-y): run_asset table + occurrence.run_asset_id (0005, RLS)"
```

---

### Task 2: Occurrence asset dimension in the outbox store

**Files:**
- Modify: `src/recon/findings/store.py` (`Occurrence`, `_identity`, `record_finding`)
- Test: `src/recon/findings/store_asset_test.py` (Create)

**Interfaces:**
- Consumes: `models.RunAsset` (Task 1).
- Produces: `store.Occurrence(..., run_asset_id: str | None = None, asset_url: str | None = None)`; `_identity()` now includes `asset_url`; `record_finding` persists `run_asset_id` on the occurrence row. `finding_hash` is unchanged.

- [ ] **Step 1: Write the failing test**

Create `src/recon/findings/store_asset_test.py`:

```python
"""Slice Y: the asset dimension keeps the same finding's sightings distinct per asset."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import normalize
from recon.findings.store import Occurrence, record_finding
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


def _run_with_two_assets(tenant, session_id):
    with tenant_session(tenant) as s:
        run = models.Run(tenant_id=tenant, session_id=session_id)
        s.add(run)
        s.flush()
        a1 = models.RunAsset(tenant_id=tenant, run_id=run.id, url="https://acme.io/a.js")
        a2 = models.RunAsset(tenant_id=tenant, run_id=run.id, url="https://acme.io/b.js")
        s.add_all([a1, a2])
        s.flush()
        return str(run.id), str(a1.id), str(a2.id)


def test_same_endpoint_two_assets_one_finding_two_occurrences(authorized_session):
    tenant, session_id = authorized_session
    run_id, a1, a2 = _run_with_two_assets(tenant, session_id)
    ep = normalize.normalize_endpoint("GET", "https://api.acme.io/users/1")
    # Identical path + offsets in both assets — only the asset dimension keeps them apart.
    common = dict(host=ep.host, raw_url="/users/1", source_path="input.js",
                  offset_start=5, offset_end=9)
    with tenant_session(tenant) as s:
        r1 = record_finding(s, tenant_id=tenant, run_id=run_id, finding_type=FindingType.ENDPOINT,
                            value=ep.value, path="input.js",
                            occurrence=Occurrence(run_asset_id=a1, asset_url="https://acme.io/a.js", **common))
        r2 = record_finding(s, tenant_id=tenant, run_id=run_id, finding_type=FindingType.ENDPOINT,
                            value=ep.value, path="input.js",
                            occurrence=Occurrence(run_asset_id=a2, asset_url="https://acme.io/b.js", **common))
    assert r1.finding_hash == r2.finding_hash
    assert r1.finding_created and not r2.finding_created  # one finding
    assert r1.occurrence_created and r2.occurrence_created  # two sightings
    with tenant_session(tenant) as s:
        assert s.execute(select(func.count()).select_from(models.Finding)
                         .where(models.Finding.run_id == run_id)).scalar() == 1
        occs = s.execute(select(models.FindingOccurrence)
                         .where(models.FindingOccurrence.finding_id == r1.finding_id)).scalars().all()
        assert {str(o.run_asset_id) for o in occs} == {a1, a2}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/findings/store_asset_test.py -v`
Expected: FAIL — `Occurrence.__init__` has no `run_asset_id`/`asset_url` (TypeError), or both sightings collapse to one occurrence.

- [ ] **Step 3: Add the fields, identity key, and row write**

In `src/recon/findings/store.py`, add two fields to `Occurrence` (after `verified`):

```python
    # Slice Y asset dimension. asset_url is part of occurrence identity so the same
    # finding stays distinct per asset; run_asset_id is stored for reveal routing.
    run_asset_id: str | None = None
    asset_url: str | None = None
```

Add `asset_url` to `_identity()` (inside the returned dict):

```python
            "col": self.col,
            "asset_url": self.asset_url,
        }
```

Add `run_asset_id` to the occurrence insert `.values(...)` in `record_finding` (after `finding_id=finding_id,`):

```python
            finding_id=finding_id,
            run_asset_id=occurrence.run_asset_id,
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/findings/store_asset_test.py -v`
Expected: PASS.

- [ ] **Step 5: Confirm existing store + normalize tests still pass**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/findings/store_test.py src/recon/findings/normalize_test.py -v`
Expected: PASS (the legacy occurrences now hash with `asset_url=None`; pre-prod, no data churn).

- [ ] **Step 6: Commit**

```bash
git add src/recon/findings/store.py src/recon/findings/store_asset_test.py
git commit -m "feat(slice-y): asset dimension in occurrence identity + row"
```

---

### Task 3: `run_asset` read/write helpers

**Files:**
- Create: `src/recon/runs/assets.py`
- Create: `src/recon/runs/assets_test.py`

**Interfaces:**
- Consumes: `models.RunAsset` (Task 1), `domain.AssetStatus`.
- Produces:
  - `assets.AssetRow` (frozen: `id, url, input_ref, fetch_status, analyze_status`).
  - `assets.seed_pending(session, *, tenant_id, run_id, urls: list[str]) -> None` — idempotent bulk insert on `(run_id, url)`.
  - `assets.list_for_run(tenant_id, run_id) -> list[AssetRow]` — own read txn, ordered by url.
  - `assets.set_fetch_ok(session, asset_id, input_ref)`, `assets.set_fetch_failed(session, asset_id, error)`.
  - `assets.set_analyze_ok(session, asset_id)`, `assets.set_analyze_failed(session, asset_id, error)`.

- [ ] **Step 1: Write the failing test**

Create `src/recon/runs/assets_test.py`:

```python
import pytest

from recon.db import models
from recon.db.base import tenant_session
from recon.runs import assets
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


def _run(tenant, session_id):
    with tenant_session(tenant) as s:
        run = models.Run(tenant_id=tenant, session_id=session_id)
        s.add(run)
        s.flush()
        return str(run.id)


def test_seed_is_idempotent_and_listable(authorized_session):
    tenant, session_id = authorized_session
    run_id = _run(tenant, session_id)
    urls = ["https://acme.io/a.js", "https://acme.io/b.js"]
    with tenant_session(tenant) as s:
        assets.seed_pending(s, tenant_id=tenant, run_id=run_id, urls=urls)
    with tenant_session(tenant) as s:  # re-seed (redelivery) adds nothing
        assets.seed_pending(s, tenant_id=tenant, run_id=run_id, urls=urls)
    rows = assets.list_for_run(tenant, run_id)
    assert [r.url for r in rows] == urls
    assert all(r.fetch_status == "pending" for r in rows)


def test_status_setters(authorized_session):
    tenant, session_id = authorized_session
    run_id = _run(tenant, session_id)
    with tenant_session(tenant) as s:
        assets.seed_pending(s, tenant_id=tenant, run_id=run_id, urls=["https://acme.io/a.js"])
    asset_id = assets.list_for_run(tenant, run_id)[0].id
    with tenant_session(tenant) as s:
        assets.set_fetch_ok(s, asset_id, "t/r/input/deadbeef")
    with tenant_session(tenant) as s:
        assets.set_analyze_failed(s, asset_id, "boom")
    row = assets.list_for_run(tenant, run_id)[0]
    assert row.input_ref == "t/r/input/deadbeef"
    assert row.fetch_status == "ok" and row.analyze_status == "failed"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/runs/assets_test.py -v`
Expected: FAIL — `No module named 'recon.runs.assets'`.

- [ ] **Step 3: Write the module**

Create `src/recon/runs/assets.py`:

```python
"""Per-asset run state (Slice Y). One row per discovered asset; created by discover,
mutated by fetch/analyze, aggregated by the coordinator for REQ-D5 completeness.

Status setters take the caller's ``session`` so a stage can commit an asset's status
together with its side effects in that asset's own transaction (best-effort survives an
infra-error retry). ``list_for_run`` owns its read transaction and returns detached rows.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from recon.db import models
from recon.db.base import tenant_session
from recon.domain import AssetStatus

_ERR_CAP = 500  # keep a single asset's error message bounded


@dataclass(frozen=True)
class AssetRow:
    id: str
    url: str
    input_ref: str | None
    fetch_status: str
    analyze_status: str


def seed_pending(session: Session, *, tenant_id: str, run_id: str, urls: list[str]) -> None:
    if not urls:
        return
    session.execute(
        pg_insert(models.RunAsset)
        .values([
            {"tenant_id": str(tenant_id), "run_id": str(run_id), "url": u} for u in urls
        ])
        .on_conflict_do_nothing(index_elements=["run_id", "url"])
    )


def list_for_run(tenant_id: str, run_id: str) -> list[AssetRow]:
    with tenant_session(tenant_id) as session:
        rows = session.scalars(
            select(models.RunAsset)
            .where(models.RunAsset.run_id == str(run_id))
            .order_by(models.RunAsset.url)
        ).all()
        return [
            AssetRow(
                id=str(r.id), url=r.url, input_ref=r.input_ref,
                fetch_status=r.fetch_status, analyze_status=r.analyze_status,
            )
            for r in rows
        ]


def _set(session: Session, asset_id: str, values: dict) -> None:
    session.execute(
        update(models.RunAsset).where(models.RunAsset.id == asset_id).values(**values)
    )


def set_fetch_ok(session: Session, asset_id: str, input_ref: str) -> None:
    _set(session, asset_id, {"input_ref": input_ref, "fetch_status": AssetStatus.OK.value, "fetch_error": None})


def set_fetch_failed(session: Session, asset_id: str, error: str) -> None:
    _set(session, asset_id, {"fetch_status": AssetStatus.FAILED.value, "fetch_error": error[:_ERR_CAP]})


def set_analyze_ok(session: Session, asset_id: str) -> None:
    _set(session, asset_id, {"analyze_status": AssetStatus.OK.value, "analyze_error": None})


def set_analyze_failed(session: Session, asset_id: str, error: str) -> None:
    _set(session, asset_id, {"analyze_status": AssetStatus.FAILED.value, "analyze_error": error[:_ERR_CAP]})
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/runs/assets_test.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/recon/runs/assets.py src/recon/runs/assets_test.py
git commit -m "feat(slice-y): run_asset read/write helpers"
```

---

### Task 4: Discover path-guard + seed `run_asset` rows

**Files:**
- Modify: `src/recon/discover/crawl.py`
- Test: `src/recon/discover/crawl_test.py`

**Interfaces:**
- Consumes: `assets.seed_pending` (Task 3), `models.Run.target`.
- Produces: after a successful crawl, `discover_run` inserts one `run_asset(pending)` per kept URL (in the manifest-write transaction). Only bare-domain targets crawl; a target with a path or `None` no-ops (no `discover.assets` event).

- [ ] **Step 1: Write the failing tests**

Add to `src/recon/discover/crawl_test.py`:

```python
def test_discover_run_skips_target_with_path():
    # A single-asset URL target is NOT a crawl — no event, no rows (backward compat).
    with patch("recon.discover.crawl.queries.latest_assets_event", return_value=None), \
         patch("recon.discover.crawl._load_target", return_value=("https://acme.io/app.js", "s")), \
         patch("recon.discover.crawl.harness.run_crawl") as run_crawl:
        crawl.discover_run(MagicMock(), tenant_id="t", run_id="r", job_id="j")
    run_crawl.assert_not_called()


def test_discover_run_seeds_run_asset_rows():
    engagement = SimpleNamespace(scope_hosts=["acme.io"], authorization_ack=True)
    seeded = {}
    with patch("recon.discover.crawl.record_event", return_value=MagicMock()), \
         patch("recon.discover.crawl.publish"), \
         patch("recon.discover.crawl.assets.seed_pending",
               side_effect=lambda s, **k: seeded.update(k)):
        for p in _patches(
            katana_urls=["https://acme.io/app.js", "https://acme.io/vendor.js"],
            validated={"https://acme.io/app.js", "https://acme.io/vendor.js"},
            engagement=engagement,
        ):
            p.start()
        try:
            crawl.discover_run(MagicMock(), tenant_id="t", run_id="r", job_id="j")
        finally:
            patch.stopall()
    assert seeded["urls"] == ["https://acme.io/app.js", "https://acme.io/vendor.js"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/discover/crawl_test.py -k "path or seeds" -v`
Expected: FAIL — crawl still runs for a path target; `assets` not imported/seeded.

- [ ] **Step 3: Add the path-guard and seeding**

In `src/recon/discover/crawl.py`, add the import:

```python
from recon.runs import assets
```

Add a path check helper near `_host`:

```python
def _is_bare_domain(target: str) -> bool:
    """A crawl target must be a bare host (no path) — a target with a path is a
    single asset URL and stays on the legacy single-asset path (Slice Y backward
    compat; also closes the Slice X 'crawls any in-scope target' latent guard)."""
    t = target if "://" in target else f"https://{target}"
    path = urlsplit(t).path
    return path in ("", "/")
```

In `discover_run`, after the `if not target: return` guard, add:

```python
    if not _is_bare_domain(target):
        return  # a single asset URL, not a domain crawl — legacy path handles it
```

In the manifest-write transaction, seed the rows alongside the event:

```python
    with tenant_session(tenant_id) as session:
        assets.seed_pending(session, tenant_id=tenant_id, run_id=run_id, urls=kept)
        event = record_event(
            session, tenant_id=tenant_id, run_id=run_id,
            event_type="discover.assets",
            payload={"count": len(kept), "assets_ref": assets_ref, "status": status},
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/discover/crawl_test.py -v`
Expected: PASS (all discover tests, including the two new ones).

- [ ] **Step 5: Commit**

```bash
git add src/recon/discover/crawl.py src/recon/discover/crawl_test.py
git commit -m "feat(slice-y): discover path-guard + seed run_asset rows"
```

---

### Task 5: Cooperative stage interrupt (pause/cancel mid-loop)

**Files:**
- Modify: `src/recon/queue/retry.py` (add `ControlInterrupt`)
- Modify: `src/recon/worker/main.py` (catch it; pass `job_id` to fetch/analyze)
- Test: `src/recon/worker/main_test.py`

**Interfaces:**
- Produces: `retry.ControlInterrupt(kind: str)` where `kind ∈ {"pause","cancel"}`. `worker._run_stage_work` now passes `job_id` to `fetch_run` and `analyze_run`. `process_message` catches `ControlInterrupt` before the generic handler and transitions PAUSED/CANCELLED (mirroring the stub-loop), acking without advancing.

- [ ] **Step 1: Add the exception**

In `src/recon/queue/retry.py`, add (next to `FatalError`/`RetryableError`):

```python
class ControlInterrupt(Exception):
    """A long stage observed a pause/cancel request mid-work and stopped cooperatively.

    Carries ``kind`` ('pause'|'cancel'); the worker maps it to the same transition its
    pre-work checkpoints do, so a multi-asset stage honors REQ-A4 without waiting for
    the whole loop. Not a failure — never retried or dead-lettered."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind
```

- [ ] **Step 2: Write the failing worker test**

Add to `src/recon/worker/main_test.py` (match the file's existing import/fixture style):

```python
def test_control_interrupt_pauses_without_advancing(monkeypatch, redis, authorized_session):
    from recon.domain import RunStage
    from recon.queue import retry
    from recon.runs import coordinator, queries
    from recon.worker import main

    tenant, session_id = authorized_session
    view = coordinator.start_run(redis, tenant_id=tenant, session_id=session_id, target="acme.io")
    # Drive the discover message; make the stage raise a cancel interrupt.
    monkeypatch.setattr(main, "_run_stage_work",
                        lambda *a, **k: (_ for _ in ()).throw(retry.ControlInterrupt("cancel")))
    advanced = {"n": 0}
    monkeypatch.setattr(coordinator, "advance", lambda *a, **k: advanced.__setitem__("n", advanced["n"] + 1))

    processed = main.run_once(redis, "worker-test", block_ms=50)

    assert processed >= 1
    assert advanced["n"] == 0  # a cancel must NOT advance the run
    assert queries.get_run_flags(tenant, view.id).state == "cancelled"
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/worker/main_test.py::test_control_interrupt_pauses_without_advancing -v`
Expected: FAIL — `ControlInterrupt` currently falls into `_handle_failure` → run goes `failed`/retried, not `cancelled`.

- [ ] **Step 4: Pass `job_id` and catch the interrupt**

In `src/recon/worker/main.py`, update `_run_stage_work` to pass `job_id`:

```python
    elif stage == RunStage.FETCHING:
        fetch.fetch_run(redis, tenant_id=tenant_id, run_id=run_id, job_id=job_id)
    elif stage == RunStage.ANALYZING:
        analyze.analyze_run(redis, tenant_id=tenant_id, run_id=run_id, job_id=job_id)
```

In `process_message`, change the `try/except` around `_run_stage_work` to catch the interrupt first:

```python
        try:
            for step in range(1, STUB_STEPS + 1):
                ...
            _run_stage_work(redis, stage, tenant_id=tenant_id, run_id=run_id, job_id=job_id)
        except retry.ControlInterrupt as ci:
            # A long stage saw pause/cancel mid-loop — mirror the pre-work checkpoints,
            # do NOT advance (the run is now paused/cancelled).
            if ci.kind == "cancel":
                _to_cancelled(redis, tenant_id, run_id)
            else:
                _to_paused(redis, tenant_id, run_id, stage)
            streams.ack(redis, queue, msg_id)
            return ci.kind
        except Exception as exc:  # noqa: BLE001 - failure routing is intentional
            return _handle_failure(...)  # unchanged
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/worker/main_test.py -v`
Expected: PASS (new test + existing worker tests).

- [ ] **Step 6: Commit**

```bash
git add src/recon/queue/retry.py src/recon/worker/main.py src/recon/worker/main_test.py
git commit -m "feat(slice-y): cooperative stage interrupt for mid-loop pause/cancel"
```

---

### Task 6: Multi-asset fetch loop

**Files:**
- Modify: `src/recon/fetch/fetch.py`
- Test: `src/recon/fetch/fetch_multi_test.py` (Create)

**Interfaces:**
- Consumes: `assets.list_for_run` / `set_fetch_ok` / `set_fetch_failed` (Task 3), `retry.ControlInterrupt` (Task 5), `runs.queries.get_run_flags`, `politeness.check`, `fetch_url`, `progress.beat`, `storage.put_blob`.
- Produces: `fetch.fetch_run(redis, *, tenant_id, run_id, job_id=None)` — loops `run_asset` rows when present (per-asset commit, re-check politeness, honor `Retry-After`, best-effort, heartbeat, cooperative interrupt); otherwise the unchanged single-target path.

- [ ] **Step 1: Write the failing tests**

Create `src/recon/fetch/fetch_multi_test.py`:

```python
"""Slice Y multi-asset fetch loop — DB-backed, fetch_url stubbed."""

from __future__ import annotations

import pytest

from recon.db.base import tenant_session
from recon.fetch import egress, fetch
from recon.queue import retry
from recon.runs import assets
from recon.runs import service

pytestmark = pytest.mark.integration


def _crawl_run(redis, tenant, session_id, urls):
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id, target="acme.io")
    with tenant_session(tenant) as s:
        assets.seed_pending(s, tenant_id=tenant, run_id=view.id, urls=urls)
    return view.id


def test_fetch_loop_records_ok_and_failed_per_asset(redis, authorized_session, monkeypatch):
    tenant, session_id = authorized_session
    run_id = _crawl_run(redis, tenant, session_id, ["https://acme.io/a.js", "https://acme.io/bad.js"])

    def fake_fetch(url, scope, **kw):
        if url.endswith("bad.js"):
            raise retry.FatalError("HTTP 404")
        return b'fetch("/api/x");'

    monkeypatch.setattr(fetch, "fetch_url", fake_fetch)
    fetch.fetch_run(redis, tenant_id=tenant, run_id=run_id, job_id="j")

    rows = {r.url: r for r in assets.list_for_run(tenant, run_id)}
    assert rows["https://acme.io/a.js"].fetch_status == "ok"
    assert rows["https://acme.io/a.js"].input_ref is not None
    assert rows["https://acme.io/bad.js"].fetch_status == "failed"


def test_fetch_loop_is_idempotent_on_redelivery(redis, authorized_session, monkeypatch):
    tenant, session_id = authorized_session
    run_id = _crawl_run(redis, tenant, session_id, ["https://acme.io/a.js"])
    monkeypatch.setattr(fetch, "fetch_url", lambda *a, **k: b"one();")
    fetch.fetch_run(redis, tenant_id=tenant, run_id=run_id, job_id="j")

    def _must_not_fetch(*a, **k):
        raise AssertionError("re-fetched a terminal asset")

    monkeypatch.setattr(fetch, "fetch_url", _must_not_fetch)
    fetch.fetch_run(redis, tenant_id=tenant, run_id=run_id, job_id="j")  # no-op


def test_fetch_loop_honors_cancel(redis, authorized_session, monkeypatch):
    from recon.runs import service as run_service
    tenant, session_id = authorized_session
    run_id = _crawl_run(redis, tenant, session_id, ["https://acme.io/a.js", "https://acme.io/b.js"])
    run_service.request_cancel(redis, tenant_id=tenant, run_id=run_id)
    monkeypatch.setattr(fetch, "fetch_url", lambda *a, **k: b"x();")
    with pytest.raises(retry.ControlInterrupt) as ci:
        fetch.fetch_run(redis, tenant_id=tenant, run_id=run_id, job_id="j")
    assert ci.value.kind == "cancel"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/fetch/fetch_multi_test.py -v`
Expected: FAIL — `fetch_run` has no `job_id` param and no multi-asset branch.

- [ ] **Step 3: Implement the loop**

In `src/recon/fetch/fetch.py`, add imports:

```python
from recon.progress import heartbeat as progress
from recon.runs import assets as run_assets
from recon.runs import queries as run_queries
```

Change the `fetch_run` signature and add the branch at the top of its body:

```python
def fetch_run(redis: Redis, *, tenant_id: str, run_id: str, job_id: str | None = None) -> None:
    rows = run_assets.list_for_run(tenant_id, run_id)
    if rows:
        _fetch_assets(redis, tenant_id=tenant_id, run_id=run_id, job_id=job_id, rows=rows)
        return
    # ---- legacy single-target path below (unchanged) ----
    with tenant_session(tenant_id) as session:
        ...
```

Add the loop helpers at the end of the module:

```python
def _check_control(tenant_id: str, run_id: str) -> None:
    """Raise ControlInterrupt if a pause/cancel was requested (REQ-A4, mid-loop)."""
    flags = run_queries.get_run_flags(tenant_id, run_id)
    if flags and flags.cancel_requested:
        raise retry.ControlInterrupt("cancel")
    if flags and flags.pause_requested:
        raise retry.ControlInterrupt("pause")


def _await_host_slot(redis: Redis, host: str, *, tenant_id: str, run_id: str, job_id: str | None,
                     settings) -> None:
    """Acquire the per-host politeness slot, re-checking until check() returns 0.0.

    politeness.check is a CONSUMING acquire — only the caller it returns 0.0 to took the
    slot + incremented the global budget. A sleep-once-then-proceed would skip both, so
    we loop; each wait heartbeats so the lease never lapses."""
    while (wait := politeness.check(redis, host, settings=settings)) > 0:
        _beat_sleep(redis, tenant_id=tenant_id, run_id=run_id, job_id=job_id, seconds=wait)


def _beat_sleep(redis: Redis, *, tenant_id: str, run_id: str, job_id: str | None, seconds: float) -> None:
    remaining = seconds
    step = get_settings().crawl_heartbeat_interval_seconds
    while remaining > 0:
        nap = min(step, remaining)
        time.sleep(nap)
        remaining -= nap
        if job_id:
            progress.beat(redis, tenant_id=tenant_id, run_id=run_id, job_id=job_id,
                          done=0, total=0, emit_event=False)


def _fetch_assets(redis: Redis, *, tenant_id: str, run_id: str, job_id: str | None,
                  rows: list[run_assets.AssetRow]) -> None:
    engagement = _authorized_engagement(tenant_id, run_id)
    settings = get_settings()
    total = len(rows)
    for i, asset in enumerate(rows, 1):
        if asset.fetch_status in ("ok", "failed") or asset.input_ref:
            continue
        _check_control(tenant_id, run_id)  # REQ-A4
        host = (urlsplit(asset.url).hostname or "").lower()
        if host:
            _await_host_slot(redis, host, tenant_id=tenant_id, run_id=run_id,
                             job_id=job_id, settings=settings)
        try:
            content = fetch_url(
                asset.url, engagement.scope_hosts,
                timeout_s=settings.fetch_timeout_seconds, max_bytes=settings.max_fetch_bytes,
            )
        except (egress.EgressBlocked, retry.FatalError, retry.RetryableError) as exc:
            with tenant_session(tenant_id) as s:
                run_assets.set_fetch_failed(s, asset.id, str(exc))  # per-asset commit
            retry_after = getattr(exc, "retry_after", None)
            if retry_after:  # honor the target's host-wide backoff even though we drop it
                _beat_sleep(redis, tenant_id=tenant_id, run_id=run_id, job_id=job_id,
                            seconds=float(retry_after))
            continue
        key = storage.put_blob(tenant_id, run_id, "input", content)
        with tenant_session(tenant_id) as s:
            run_assets.set_fetch_ok(s, asset.id, key)  # per-asset commit
        if job_id:
            progress.beat(redis, tenant_id=tenant_id, run_id=run_id, job_id=job_id,
                          done=i, total=total)
        log.info("fetch.asset_done", run_id=run_id, url=asset.url, bytes=len(content))


def _authorized_engagement(tenant_id: str, run_id: str):
    with tenant_session(tenant_id) as session:
        run = session.get(Run, run_id)
        session_id = str(run.session_id) if run is not None else None
    engagement = sessions_service.get_session(tenant_id, session_id)
    if engagement is None or not engagement.authorization_ack:
        raise retry.FatalError("session is not authorized for egress")
    return engagement
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/fetch/fetch_multi_test.py src/recon/fetch/fetch_test.py -v`
Expected: PASS (new multi-asset tests + the unchanged single-target tests).

- [ ] **Step 5: Commit**

```bash
git add src/recon/fetch/fetch.py src/recon/fetch/fetch_multi_test.py
git commit -m "feat(slice-y): multi-asset fetch loop (per-asset commit, re-check politeness)"
```

---

### Task 7: Multi-asset analyze loop

**Files:**
- Modify: `src/recon/findings/analyze.py`
- Test: `src/recon/findings/analyze_multi_test.py` (Create)

**Interfaces:**
- Consumes: `assets.list_for_run` / `set_analyze_ok` / `set_analyze_failed` (Task 3), `retry.ControlInterrupt` + `_check_control` pattern, `store.Occurrence(run_asset_id, asset_url)` (Task 2).
- Produces: `analyze.analyze_run(redis, *, tenant_id, run_id, job_id=None)` — loops fetched `run_asset` rows (skip analyze-terminal), tags occurrences with `run_asset_id`+`asset_url`, per-asset commit, best-effort, heartbeat, cooperative interrupt; otherwise the unchanged single-blob path.

- [ ] **Step 1: Write the failing test**

Create `src/recon/findings/analyze_multi_test.py`:

```python
"""Slice Y multi-asset analyze loop — findings attributed + deduped across assets."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.findings import analyze
from recon.runs import assets, service

pytestmark = pytest.mark.integration


def test_analyze_loop_dedups_across_assets_with_attribution(redis, authorized_session):
    tenant, session_id = authorized_session
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id, target="acme.io")
    run_id = view.id
    # Two assets, both calling the same endpoint -> one finding, two occurrences.
    src = b'fetch("/api/shared");'
    k1 = storage.put_blob(tenant, run_id, "input", src)
    k2 = storage.put_blob(tenant, run_id, "input", src + b" ")  # distinct bytes -> distinct key
    with tenant_session(tenant) as s:
        assets.seed_pending(s, tenant_id=tenant, run_id=run_id,
                            urls=["https://acme.io/a.js", "https://acme.io/b.js"])
    rows = assets.list_for_run(tenant, run_id)
    with tenant_session(tenant) as s:
        assets.set_fetch_ok(s, rows[0].id, k1)
        assets.set_fetch_ok(s, rows[1].id, k2)

    analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id, job_id="j")

    with tenant_session(tenant) as s:
        findings = s.execute(select(models.Finding).where(models.Finding.run_id == run_id)
                             ).scalars().all()
        endpoint = [f for f in findings if f.type == "endpoint"]
        assert len(endpoint) == 1
        occ = s.execute(select(models.FindingOccurrence)
                        .where(models.FindingOccurrence.finding_id == endpoint[0].id)).scalars().all()
        assert {str(o.run_asset_id) for o in occ} == {rows[0].id, rows[1].id}
    assert all(a.analyze_status == "ok" for a in assets.list_for_run(tenant, run_id))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/findings/analyze_multi_test.py -v`
Expected: FAIL — `analyze_run` has no `job_id` and no multi-asset branch.

- [ ] **Step 3: Implement the loop**

In `src/recon/findings/analyze.py`, add imports:

```python
from recon.progress import heartbeat as progress
from recon.queue import retry
from recon.runs import assets as run_assets
from recon.runs import queries as run_queries
```

Change the signature and add the branch at the top of `analyze_run`:

```python
def analyze_run(redis: Redis, *, tenant_id: str, run_id: str, job_id: str | None = None) -> Coverage:
    rows = run_assets.list_for_run(tenant_id, run_id)
    if rows:
        return _analyze_assets(redis, tenant_id=tenant_id, run_id=run_id, job_id=job_id, rows=rows)
    # ---- legacy single-blob path below (unchanged) ----
    with tenant_session(tenant_id) as session:
        ...
```

Refactor the per-blob work so both paths share it. Extract the body of the existing single-blob analyze (from `raw = storage.get_blob(...)` through writing occurrences + the coverage event) into a helper `_analyze_blob(session, redis, *, tenant_id, run_id, input_ref, source_map_ref, run_asset_id, asset_url) -> Coverage` that takes an open `session`, and have `store.Occurrence(...)` in `_record_endpoint`/`_record_secret` receive `run_asset_id`/`asset_url` (thread them through). Then:

```python
def _analyze_assets(redis, *, tenant_id, run_id, job_id, rows) -> Coverage:
    total = sum(1 for a in rows if a.fetch_status == "ok")
    done = 0
    agg = Coverage(0, 0, 0)
    for asset in rows:
        if asset.fetch_status != "ok" or asset.analyze_status in ("ok", "failed"):
            continue
        _check_control(tenant_id, run_id)  # REQ-A4 (same helper shape as fetch)
        done += 1
        try:
            with tenant_session(tenant_id) as session:  # per-asset commit (findings + status)
                cov = _analyze_blob(
                    session, redis, tenant_id=tenant_id, run_id=run_id,
                    input_ref=asset.input_ref, source_map_ref=None,
                    run_asset_id=asset.id, asset_url=asset.url,
                )
                run_assets.set_analyze_ok(session, asset.id)
            agg = _merge_coverage(agg, cov)
        except Exception as exc:  # noqa: BLE001 - per-asset best-effort
            with tenant_session(tenant_id) as session:
                run_assets.set_analyze_failed(session, asset.id, str(exc))
        if job_id:
            progress.beat(redis, tenant_id=tenant_id, run_id=run_id, job_id=job_id,
                          done=done, total=total)
    return agg
```

Add `_check_control` (identical shape to fetch's) and a small `_merge_coverage(a, b)` that sums the integer fields. Note: the per-asset `analyze.coverage` event is emitted inside `_analyze_blob`; the read side already treats the highest-id event as authoritative (`findings/queries.py:124-126`), so a per-asset event stream is tolerated.

- [ ] **Step 4: Run the test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/findings/analyze_multi_test.py src/recon/findings/analyze_test.py -v`
Expected: PASS (new + unchanged single-blob analyze tests).

- [ ] **Step 5: Commit**

```bash
git add src/recon/findings/analyze.py src/recon/findings/analyze_multi_test.py
git commit -m "feat(slice-y): multi-asset analyze loop (asset-tagged, per-asset commit)"
```

---

### Task 8: Completeness — `DONE` vs `PARTIAL`

**Files:**
- Modify: `src/recon/runs/coordinator.py`
- Test: `src/recon/runs/coordinator_completeness_test.py` (Create)

**Interfaces:**
- Consumes: `assets.list_for_run` (Task 3), `discover.queries.latest_assets_event`, `domain.AssetStatus`, `service.transition`.
- Produces: `coordinator.advance` finalizes a crawl run to `DONE` iff crawl clean + all assets fetched + all analyzed, else `PARTIAL`, with computed `completeness`. Legacy runs (no `discover.assets` event) keep the hardcoded `DONE`.

- [ ] **Step 1: Write the failing test**

Create `src/recon/runs/coordinator_completeness_test.py`:

```python
import pytest

from recon.db.base import tenant_session
from recon.domain import RunStage, RunState
from recon.events.log import record_event
from recon.runs import assets, coordinator, queries, service

pytestmark = pytest.mark.integration


def _crawl_run_at_correlating(redis, tenant, session_id, *, crawl_status, urls):
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id, target="acme.io")
    with tenant_session(tenant) as s:
        assets.seed_pending(s, tenant_id=tenant, run_id=view.id, urls=urls)
        record_event(s, tenant_id=tenant, run_id=view.id, event_type="discover.assets",
                     payload={"count": len(urls), "assets_ref": "x", "status": crawl_status})
    # walk to CORRELATING so DONE/PARTIAL is legal
    for st in (RunState.DISCOVERING, RunState.FETCHING, RunState.INGESTING,
               RunState.ANALYZING, RunState.CORRELATING):
        service.transition(redis, tenant_id=tenant, run_id=view.id, to_state=st,
                           stage=RunStage(st.value))
    return view.id


def _finalize(redis, tenant, run_id):
    coordinator.advance(redis, tenant_id=tenant, run_id=run_id, completed=RunStage.CORRELATING)
    return queries.get_run_flags(tenant, run_id).state


def test_all_ok_is_done(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _crawl_run_at_correlating(redis, tenant, session_id,
                                       crawl_status="ok", urls=["https://acme.io/a.js"])
    aid = assets.list_for_run(tenant, run_id)[0].id
    with tenant_session(tenant) as s:
        assets.set_fetch_ok(s, aid, "k"); assets.set_analyze_ok(s, aid)
    assert _finalize(redis, tenant, run_id) == "done"


def test_one_fetch_fail_is_partial(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _crawl_run_at_correlating(redis, tenant, session_id,
                                       crawl_status="ok", urls=["https://acme.io/a.js"])
    aid = assets.list_for_run(tenant, run_id)[0].id
    with tenant_session(tenant) as s:
        assets.set_fetch_failed(s, aid, "404")
    assert _finalize(redis, tenant, run_id) == "partial"


def test_capped_crawl_is_partial(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _crawl_run_at_correlating(redis, tenant, session_id,
                                       crawl_status="capped", urls=["https://acme.io/a.js"])
    aid = assets.list_for_run(tenant, run_id)[0].id
    with tenant_session(tenant) as s:
        assets.set_fetch_ok(s, aid, "k"); assets.set_analyze_ok(s, aid)
    assert _finalize(redis, tenant, run_id) == "partial"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/runs/coordinator_completeness_test.py -v`
Expected: FAIL — `advance` always finalizes `done`.

- [ ] **Step 3: Implement the computed finalize**

In `src/recon/runs/coordinator.py`, add imports:

```python
from recon.discover import queries as discover_queries
from recon.domain import AssetStatus
from recon.runs import assets as run_assets
```

Replace the finalize branch in `advance`:

```python
    try:
        to_state, completeness = _finalize_state(tenant_id, run_id)
        service.transition(
            redis, tenant_id=tenant_id, run_id=run_id,
            to_state=to_state, extra_values={"completeness": completeness},
        )
    except (service.TransitionConflict, sm.InvalidTransition):
        pass


def _finalize_state(tenant_id: str, run_id: str) -> tuple[RunState, dict]:
    """DONE vs PARTIAL from per-asset status. Discriminator is the discover.assets
    event (a crawl run), NOT the row count — a timed-out zero-asset crawl has no rows
    but must still be PARTIAL."""
    event = discover_queries.latest_assets_event(tenant_id, run_id)
    if event is None:  # legacy single-asset run — unchanged behavior
        return RunState.DONE, {"fetch_ok": True, "analyze_ok": True}
    rows = run_assets.list_for_run(tenant_id, run_id)
    crawl_ok = event.get("status") == "ok"
    fetch_ok = crawl_ok and all(a.fetch_status == AssetStatus.OK.value for a in rows)
    analyze_ok = fetch_ok and all(a.analyze_status == AssetStatus.OK.value for a in rows)
    to_state = RunState.DONE if (fetch_ok and analyze_ok) else RunState.PARTIAL
    return to_state, {"fetch_ok": fetch_ok, "analyze_ok": analyze_ok}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/runs/coordinator_completeness_test.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/recon/runs/coordinator.py src/recon/runs/coordinator_completeness_test.py
git commit -m "feat(slice-y): compute DONE vs PARTIAL from per-asset status"
```

---

### Task 9: Reveal routing + `revealable` read-gate

**Files:**
- Modify: `src/recon/probe/reveal.py`
- Modify: `src/recon/findings/queries.py`
- Test: `src/recon/probe/reveal_asset_test.py` (Create), `src/recon/findings/queries_reveal_redaction_test.py` (Modify — add a crawl-run case)

**Interfaces:**
- Consumes: `models.FindingOccurrence.run_asset_id` (Task 1), `models.RunAsset.input_ref`.
- Produces: `reveal._load_target` slices the chosen occurrence's `run_asset.input_ref` (fallback `run.input_ref`); `_reveal_occurrence` surfaces the chosen occurrence's `run_asset_id`. `queries._finding_view` computes `revealable` from the occurrence's asset blob ref.

- [ ] **Step 1: Write the failing test (reveal routes to the asset blob)**

Create `src/recon/probe/reveal_asset_test.py`:

```python
"""Slice Y: a crawl-run secret reveals by slicing its own asset blob."""

from __future__ import annotations

import pytest

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import normalize
from recon.findings.store import Occurrence, record_finding
from recon.probe import reveal
from recon.runs import assets, service

pytestmark = pytest.mark.integration


def test_reveal_slices_the_occurrences_asset_blob(redis, authorized_session):
    tenant, session_id = authorized_session
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id, target="acme.io")
    run_id = view.id
    token = "AKIA" + "I" * 16  # format-broken placeholder shape
    blob = f'const k = "{token}";'.encode()
    key = storage.put_blob(tenant, run_id, "input", blob)
    with tenant_session(tenant) as s:
        assets.seed_pending(s, tenant_id=tenant, run_id=run_id, urls=["https://acme.io/a.js"])
    asset = assets.list_for_run(tenant, run_id)[0]
    with tenant_session(tenant) as s:
        assets.set_fetch_ok(s, asset.id, key)
    start = blob.index(token.encode()); end = start + len(token)
    value = normalize.normalize_secret_value(token, "aws-access-key-id")
    with tenant_session(tenant) as s:
        record_finding(s, tenant_id=tenant, run_id=run_id, finding_type=FindingType.SECRET,
                       value=value, path="input.js",
                       occurrence=Occurrence(run_asset_id=asset.id, asset_url=asset.url,
                                             source_path="input.js", offset_start=start, offset_end=end),
                       attributes={"rule": "aws-access-key-id"})
        fh = normalize.finding_hash(FindingType.SECRET.value, value, "input.js")

    outcome = reveal.reveal_secret(tenant, run_id, fh)
    assert outcome is not None and outcome.revealed and outcome.value == token
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/probe/reveal_asset_test.py -v`
Expected: FAIL — `_Target.input_ref` is `run.input_ref` (NULL for the crawl run) → `source_gone`.

- [ ] **Step 3: Route reveal to the asset blob**

In `src/recon/probe/reveal.py`, make `_reveal_occurrence` return the chosen occurrence (it already does) and in `_load_target` resolve its asset blob. Replace the `_Target` build:

```python
        occurrence = _reveal_occurrence(finding.occurrences)
        input_ref = run.input_ref
        if occurrence is not None and occurrence.run_asset_id is not None:
            asset = session.get(models.RunAsset, occurrence.run_asset_id)
            if asset is not None and asset.input_ref:
                input_ref = asset.input_ref
        return _Target(
            input_ref=input_ref,
            rule=str((finding.attributes or {}).get("rule", "")),
            value=finding.value,
            offset_start=None if occurrence is None else occurrence.offset_start,
            offset_end=None if occurrence is None else occurrence.offset_end,
            source_path=None if occurrence is None else occurrence.source_path,
            line=None if occurrence is None else occurrence.line,
        )
```

- [ ] **Step 4: Run the reveal test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/probe/reveal_asset_test.py src/recon/probe/reveal_test.py -v`
Expected: PASS (asset routing + unchanged legacy reveal).

- [ ] **Step 5: Fix the `revealable` read-gate**

In `src/recon/findings/queries.py`, the run findings read loads `run.input_ref`. Load the run's assets once and pass a `{run_asset_id: input_ref}` map, then compute `revealable` from the occurrence's asset. In `get_findings` (the function around line 100-121), build the map:

```python
        asset_refs = {
            str(a.id): a.input_ref
            for a in session.scalars(
                select(models.RunAsset).where(models.RunAsset.run_id == str(run_id))
            ).all()
        }
```

Pass `asset_refs` and `run.input_ref` into `_finding_view`, and change its `revealable`:

```python
def _finding_view(finding, triage_row=None, run_input_ref=None, asset_refs=None):
    asset_refs = asset_refs or {}
    ...
    def _blob_for(o):
        return asset_refs.get(str(o.run_asset_id)) if o.run_asset_id else run_input_ref
    revealable = bool(
        is_secret
        and any(
            o.offset_start is not None and o.offset_end is not None and _blob_for(o)
            for o in finding.occurrences
        )
    )
```

- [ ] **Step 6: Write + run the `revealable` regression test**

Add to `src/recon/findings/queries_reveal_redaction_test.py` a case asserting a crawl-run secret (occurrence with a fetched `run_asset`, offsets set, `run.input_ref` NULL) reads `revealable is True`. Run:

`./.venv/Scripts/python.exe -m pytest src/recon/findings/queries_reveal_redaction_test.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/recon/probe/reveal.py src/recon/findings/queries.py src/recon/probe/reveal_asset_test.py src/recon/findings/queries_reveal_redaction_test.py
git commit -m "feat(slice-y): route reveal + revealable gate to per-asset blob"
```

---

### Task 10: Assets API — per-asset status

**Files:**
- Modify: `src/recon/discover/queries.py` (enrich manifest with per-asset status) OR `src/recon/api/runs_router.py`
- Test: `src/recon/api/runs_router_assets_test.py` (Modify)

**Interfaces:**
- Consumes: `assets.list_for_run` (Task 3), `discover.queries.get_assets_manifest`.
- Produces: `GET /runs/{id}/assets` returns each manifest asset with a `fetch_status`/`analyze_status` (left-joined from `run_asset` by url); missing rows report `pending`.

- [ ] **Step 1: Write the failing test**

Add to `src/recon/api/runs_router_assets_test.py` an integration test: seed a manifest + `run_asset` rows with mixed statuses, GET `/runs/{id}/assets`, assert each returned asset carries `fetch_status`/`analyze_status`. (Mirror the file's existing client + tenant-header setup.)

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/api/runs_router_assets_test.py -v`
Expected: FAIL — assets lack status fields.

- [ ] **Step 3: Enrich the manifest read**

In `src/recon/discover/queries.py`, add a function that merges status onto the manifest:

```python
from recon.runs import assets as run_assets


def get_assets_with_status(tenant_id: str, run_id: str) -> dict | None:
    manifest = get_assets_manifest(tenant_id, run_id)
    if manifest is None:
        return None
    status_by_url = {a.url: a for a in run_assets.list_for_run(tenant_id, run_id)}
    for entry in manifest.get("assets", []):
        row = status_by_url.get(entry["url"])
        entry["fetch_status"] = row.fetch_status if row else "pending"
        entry["analyze_status"] = row.analyze_status if row else "pending"
    return manifest
```

Point the router at it: in `src/recon/api/runs_router.py::get_run_assets`, replace `get_assets_manifest` with `get_assets_with_status`.

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/api/runs_router_assets_test.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/recon/discover/queries.py src/recon/api/runs_router.py src/recon/api/runs_router_assets_test.py
git commit -m "feat(slice-y): per-asset fetch/analyze status on the assets API"
```

---

### Task 11: Front-end — per-asset status, occurrence attribution, PARTIAL badge

**Files:**
- Modify: `web/src/` assets inventory component + findings/occurrence view + run status badge (follow the slice-UI0 component layout; locate via `web/src` grep for the assets inventory and the terminal-state badge)
- Test: colocated `*.test.tsx` (Vitest)

**Interfaces:**
- Consumes: the enriched `GET /runs/{id}/assets` (Task 10, `fetch_status`/`analyze_status` per asset); the finding view's occurrence `asset_url` (surface it in the occurrence read if not already — add `asset_url` to `_occurrence_view` in `src/recon/findings/queries.py`); the run `state` including `partial`.

- [ ] **Step 1: Surface `asset_url` on the occurrence read**

In `src/recon/findings/queries.py::_occurrence_view`, add `asset_url` to the returned occurrence view (join via `run_asset_id` → url, or add a lightweight lookup) so the FE can show attribution. Add/extend the colocated backend test asserting `asset_url` is present for a crawl-run occurrence.

- [ ] **Step 2: Write the failing Vitest for the inventory badges**

In the assets-inventory test, render with an assets payload carrying mixed `fetch_status`/`analyze_status` and assert each row shows its status; add a case for a `partial` run rendering the PARTIAL badge distinctly from `done`.

- [ ] **Step 3: Run to verify it fails**

Run: `cd web && npm test`
Expected: FAIL — components don't render per-asset status / PARTIAL badge yet.

- [ ] **Step 4: Implement the UI**

Add per-asset `fetch_status`/`analyze_status` chips to the inventory rows; show `asset_url` on each occurrence in the finding detail; add a `partial` case to the run terminal-state badge (distinct label/color from `done`).

- [ ] **Step 5: Run tests + lint to verify they pass**

Run: `cd web && npm test && npm run lint`
Expected: PASS + clean.

- [ ] **Step 6: Commit**

```bash
git add web/ src/recon/findings/queries.py
git commit -m "feat(slice-y): UI per-asset status, occurrence attribution, PARTIAL badge"
```

---

### Task 12: Integration — multi-asset crawl end-to-end

**Files:**
- Test: `src/recon/discover/multi_asset_integration_test.py` (Create)

**Interfaces:**
- Consumes: the whole pipeline (discover→fetch→analyze→finalize) + the local fixture site (compose `fixture-site`), real katana + engines.

- [ ] **Step 1: Write the integration test**

Create `src/recon/discover/multi_asset_integration_test.py` (`@pytest.mark.integration`, requires `RECON_REQUIRE_ENGINES=1` + the stack). Drive a full run against the fixture site (a bare in-scope domain serving ≥2 `.js` files), pump the worker (`run_once` in a loop until terminal), and assert: (a) ≥2 `run_asset` rows reach `fetch_status=ok`; (b) at least one finding has occurrences from ≥2 assets; (c) the run reaches `DONE`. Add a second run where one asset path 404s and assert `PARTIAL` with `completeness.fetch_ok is False`. Follow the driver pattern in `src/recon/discover/crawl_integration_test.py`.

- [ ] **Step 2: Run it (stack up, engines required)**

Run: `RECON_REQUIRE_ENGINES=1 ./.venv/Scripts/python.exe -m pytest src/recon/discover/multi_asset_integration_test.py -v -m integration`
Expected: PASS (needs `docker compose up -d`).

- [ ] **Step 3: Commit**

```bash
git add src/recon/discover/multi_asset_integration_test.py
git commit -m "test(slice-y): multi-asset crawl end-to-end (DONE + PARTIAL)"
```

---

### Task 13: Docs + debt ledger

**Files:**
- Modify: `docs/slice2-deferred-debt.md` (add a "Slice Y" section)
- Modify: the spec's §15 "As-built amendments" if anything diverged
- Modify: memory `slice-discovery-progress` (or a new `slice-y-progress`) after landing

- [ ] **Step 1: Record the debt**

Add a "Slice Y" section to `docs/slice2-deferred-debt.md` capturing: per-asset retry of transient 5xx (best-effort drops it); analyze mid-scan heartbeat (reclaim-waste, correctness-safe via the idempotent outbox); long-stage stream-reclaim strand (pre-existing, amplified); dual asset-list source of truth (manifest blob + `run_asset`); queue fan-out (model C); OpenAPI export; per-asset secret scanning of recovered source-map files.

- [ ] **Step 2: Record as-built divergences**

If implementation diverged from the spec, fill in the spec's §15.

- [ ] **Step 3: Commit**

```bash
git add docs/slice2-deferred-debt.md docs/superpowers/specs/2026-07-26-slice-y-multi-asset-design.md
git commit -m "docs(slice-y): debt ledger + as-built amendments"
```

---

## Self-Review

**1. Spec coverage:**
- run_asset table + occurrence.run_asset_id + AssetStatus + migration 0005 → Task 1. ✓
- Asset dimension in occurrence identity → Task 2. ✓
- run_asset helpers → Task 3. ✓
- Discover path-guard + seed rows → Task 4. ✓
- Multi-asset fetch loop (per-asset commit B5, re-check politeness B1, Retry-After B2, best-effort) → Task 6; cooperative interrupt (REQ-A4) → Task 5. ✓
- Multi-asset analyze loop (asset-tagged, per-asset commit, analyze-terminal skip) → Task 7. ✓
- Completeness DONE/PARTIAL (discover.assets discriminator) → Task 8. ✓
- Reveal routing + revealable read-gate (B4) → Task 9. ✓
- Assets API per-asset status → Task 10; UI → Task 11. ✓
- Migration column IF NOT EXISTS (B3) → Task 1 Step 3 + Step 6 verify. ✓
- Integration → Task 12; docs/debt → Task 13. ✓

**2. Placeholder scan:** Task 11 and Task 12 reference "locate via grep / follow the existing pattern" for FE files and the integration driver rather than exact line-level code, because those files' exact current shape (slice-UI0 components; the crawl integration driver) must be read at execution time; every backend task carries complete code. The implementer must read the referenced pattern file before writing those two tasks.

**3. Type consistency:** `AssetStatus` values (`pending`/`ok`/`failed`) are used consistently; `assets.AssetRow` fields (`id, url, input_ref, fetch_status, analyze_status`) match every consumer; `fetch_run`/`analyze_run` gain `job_id: str | None = None` consistently and the worker passes it (Task 5); `_check_control` has the same shape in fetch and analyze; `run_asset_id`/`asset_url` on `store.Occurrence` match Task 2 and the analyze/reveal consumers.
