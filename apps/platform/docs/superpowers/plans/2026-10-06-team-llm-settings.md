# Team LLM Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an admin set one LLM provider + model + key per team on a workspace Settings page; every session uses it unless the session has its own key.

**Architecture:** New RLS-protected `tenant_llm_config` table (migration 0029). New `llm/tenant_config.py` service, `llm/crypto.py` (shared encryption, breaks an import cycle), and `llm/settings_router.py` (`/settings/llm`). `load_credentials` gains a team step between session key and env key. Web: new `/settings` route + sidebar item + `features/settings/`. Also fixes the existing per-session **Test** endpoint, which always fails (event loop in a worker thread).

**Tech Stack:** FastAPI, SQLAlchemy 2 (postgresql dialect `insert … on_conflict_do_update`), Alembic, Postgres RLS, React + react-router 8, vitest + Testing Library.

**Spec:** `apps/platform/docs/superpowers/specs/2026-10-06-team-llm-settings-design.md`

## Global Constraints

- Branch `feat/team-llm-settings`, working tree `C:\Users\omri\Documents\claude-sessions\js-extractor-v2`. Backend commands run from `apps/platform`, web commands from `apps/platform/web`.
- Every tenant-table DB access goes through `tenant_session(tenant_id)`; inside it use `db.flush()`, never `db.commit()`.
- Never return, log, or render the API key or any fragment of it.
- Lookup order: session key → team key → `OPENROUTER_API_KEY` → `ANTHROPIC_API_KEY` → None. A key always comes back with its own provider.
- Migration revision id `0029_tenant_llm_config` (≤32 chars), `down_revision = "0028_finding_taxonomy_fields"`, idempotent 0017 pattern.
- Integration tests are marked `pytestmark = pytest.mark.integration`; they need live PG/Redis/MinIO. Run them in an **isolated** compose project (see "Running integration tests") — never against the user's running `platform` stack (integration tests flush its Redis).
- Ruff `F,I,UP,B,C4,SIM,PIE,RET` + `ruff format`; files ≤ ~300 lines; comments explain *why*.
- Commits: Conventional Commits, multi-line, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

### Running integration tests (used by several tasks)

From `apps/platform`, with `$TEMP/isolated-override.yml` containing:

```yaml
services:
  postgres: { ports: !reset [] }
  redis: { ports: !reset [] }
  minio: { ports: !reset [] }
  api: { ports: !reset [], image: "recon-platform:plan" }
  migrate: { image: "recon-platform:plan" }
  worker: { image: "recon-platform:plan" }
```

```bash
F="-p recon-plan -f docker-compose.yml -f $TEMP/isolated-override.yml"
docker compose $F up -d --build postgres redis minio migrate fixture-site
docker compose $F run --rm --user root -e RECON_AUTH_SECRET="" api sh -c "pip install --quiet pytest fakeredis && pytest -p no:cacheprovider -o addopts='' -v <TEST PATHS>"
docker compose $F down -v   # when finished with the task
```

The image bakes the source, so re-run `up -d --build` after code changes.

---

### Task 1: Fix the per-session Test endpoint (event loop) and share `ping_credentials`

**Files:**
- Modify: `apps/platform/src/recon/llm/service.py` (replace `test_config`, add `get_test_target`, `mark_tested`, `ping_credentials`)
- Modify: `apps/platform/src/recon/llm/router.py` (`test_llm_config`)
- Create: `apps/platform/src/recon/llm/router_test.py`

**Interfaces:**
- Produces: `async def ping_credentials(provider_name: str, model: str | None, api_key: str) -> str | None` (None = key works, else error text). Task 5 uses it.

- [ ] **Step 1: Write the failing test** — `llm/router_test.py`

```python
"""Per-session LLM config routes. The /test case must actually reach _ping."""

import pytest
from fastapi.testclient import TestClient

from recon.api.app import create_app
from recon.llm import service as llm_service
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


class _StubProvider:
    model = "stub-model"

    async def generate_structured(self, **_kwargs):
        return None


@pytest.fixture()
def client():
    return TestClient(create_app())


def test_session_key_test_reaches_ping_and_stamps_tested_at(client, tenant, monkeypatch):
    # Regression: test_config used get_event_loop().run_until_complete inside
    # run_in_threadpool, which raises RuntimeError on Python 3.11 worker threads.
    calls = []
    monkeypatch.setattr(
        llm_service,
        "build_provider",
        lambda provider, api_key, model=None: calls.append((provider, api_key)) or _StubProvider(),
    )
    sv = sessions_service.create_session(tenant, name="e", scope_hosts=["acme.io"], authorized_by="t")
    llm_service.save_config(tenant, sv.id, "anthropic", "m", "k-test")

    r = client.post(f"/sessions/{sv.id}/llm-config/test", headers={"X-Tenant-Id": tenant})

    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "provider": "anthropic", "model": "m"}
    assert calls == [("anthropic", "k-test")]
    assert llm_service.get_config(tenant, sv.id)["tested_at"] is not None


def test_session_key_test_without_key_is_400(client, tenant):
    sv = sessions_service.create_session(tenant, name="e", scope_hosts=["acme.io"], authorized_by="t")
    r = client.post(f"/sessions/{sv.id}/llm-config/test", headers={"X-Tenant-Id": tenant})
    assert r.status_code == 400
    assert r.json()["detail"] == "no config saved"
```

- [ ] **Step 2: Run it to verify it fails** (integration — see "Running integration tests")

Paths: `src/recon/llm/router_test.py`. Expected: first test FAILS with status 400 and detail containing `There is no current event loop`.

- [ ] **Step 3: Implement.** In `llm/service.py`, replace the whole `test_config` function with:

```python
def get_test_target(tenant_id: str, session_id: str) -> tuple[str, str, str] | str:
    """``(provider, model, api_key)`` to test for a session, or an error message."""
    try:
        resolved = _resolve_session_id(tenant_id, session_id)
    except ValueError:
        return "session not found"
    with tenant_session(tenant_id) as db:
        row = (
            db.query(SessionLlmConfig)
            .filter_by(session_id=uuid.UUID(resolved), tenant_id=uuid.UUID(tenant_id))
            .first()
        )
        if row is None:
            return "no config saved"
        if not row.encrypted_api_key:
            return "no API key stored"
        try:
            return row.provider, row.model, _decrypt(row.encrypted_api_key)
        except Exception as exc:
            return f"key decryption failed: {exc}"


def mark_tested(tenant_id: str, session_id: str) -> None:
    resolved = _resolve_session_id(tenant_id, session_id)
    with tenant_session(tenant_id) as db:
        row = (
            db.query(SessionLlmConfig)
            .filter_by(session_id=uuid.UUID(resolved), tenant_id=uuid.UUID(tenant_id))
            .first()
        )
        if row is not None:
            row.tested_at = dt.datetime.now(dt.UTC)


async def ping_credentials(provider_name: str, model: str | None, api_key: str) -> str | None:
    """None if the key works, else the error text.

    NOTE: must be awaited on the request's own event loop. The old version ran
    get_event_loop().run_until_complete() inside run_in_threadpool, which raises
    RuntimeError in a worker thread on Python 3.11, so Test always failed."""
    try:
        await _ping(build_provider(provider_name, api_key=api_key, model=model))
    except Exception as exc:
        return str(exc)
    return None
```

In `llm/router.py`, replace `test_llm_config` with:

```python
@router.post("/sessions/{session_id}/llm-config/test")
async def test_llm_config(
    session_id: str,
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    target = await run_in_threadpool(service.get_test_target, tenant_id, session_id)
    if isinstance(target, str):
        raise HTTPException(status_code=400, detail=target)
    provider_name, model, api_key = target
    error = await service.ping_credentials(provider_name, model, api_key)
    if error is not None:
        raise HTTPException(status_code=400, detail=error)
    await run_in_threadpool(service.mark_tested, tenant_id, session_id)
    return {"ok": True, "provider": provider_name, "model": model}
```

- [ ] **Step 4: Run tests to verify they pass** — same command. Expected: 2 passed. Also `uv run ruff check src && uv run ruff format --check src`.

- [ ] **Step 5: Commit**

```bash
git add apps/platform/src/recon/llm/service.py apps/platform/src/recon/llm/router.py apps/platform/src/recon/llm/router_test.py
git commit -m "fix(llm): make the session key Test endpoint actually reach the provider" -m "test_config ran asyncio.get_event_loop().run_until_complete(_ping(...)) inside run_in_threadpool. On Python 3.11 a worker thread has no event loop, so it raised RuntimeError, which the except swallowed: the extension's Test button always reported failure.

The route is now split across the thread boundary: get_test_target and mark_tested run in the threadpool, and ping_credentials awaits _ping on the request's own loop. ping_credentials is shared with the upcoming team settings Test.

Adds llm/router_test.py, which stubs build_provider and asserts the ping happens and tested_at is stamped. Its absence is how this shipped.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `tenant_llm_config` table, migration 0029, RLS

**Files:**
- Modify: `apps/platform/src/recon/db/models.py` (add `TenantLlmConfig` after `SessionLlmConfig`; add `TENANT_LLM_TABLES` after `LLM_TABLES`)
- Create: `apps/platform/src/recon/migrations/versions/0029_tenant_llm_config.py`
- Modify: `apps/platform/src/recon/db/llm_threat_model_rls_test.py`

**Interfaces:**
- Produces: `models.TenantLlmConfig` (columns `id, tenant_id, provider, model, encrypted_api_key, configured_at, configured_by, tested_at`), `models.TENANT_LLM_TABLES = ("tenant_llm_config",)`.

- [ ] **Step 1: Write the failing test.** In `db/llm_threat_model_rls_test.py`, change `_TABLES` and add a test:

```python
_TABLES = models.LLM_TABLES + models.THREAT_MODEL_TABLES + models.TENANT_LLM_TABLES


def test_team_llm_config_is_tenant_isolated_by_rls():
    tenant_a = sessions_service.create_tenant("team-llm-a")
    tenant_b = sessions_service.create_tenant("team-llm-b")
    with tenant_session(tenant_a) as session:
        session.add(models.TenantLlmConfig(tenant_id=tenant_a, provider="anthropic", model="m"))
    with tenant_session(tenant_a) as session:
        assert session.query(models.TenantLlmConfig).count() == 1
    with tenant_session(tenant_b) as session:
        assert session.query(models.TenantLlmConfig).count() == 0
```

- [ ] **Step 2: Run it to verify it fails** (integration; path `src/recon/db/llm_threat_model_rls_test.py`). Expected: collection error `AttributeError: module 'recon.db.models' has no attribute 'TENANT_LLM_TABLES'`.

- [ ] **Step 3: Implement the model** (in `db/models.py`, right after `class SessionLlmConfig`):

```python
class TenantLlmConfig(Base):
    """Team-wide LLM provider + key: one row per tenant (threat-model generation).

    Same encryption contract as SessionLlmConfig. A session's own key overrides it
    (recon.llm.service.load_credentials). ``configured_by`` is the admin who last
    saved it; SET NULL on user delete, matching session.created_by."""

    __tablename__ = "tenant_llm_config"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_tenant_llm_config_tenant"),
        CheckConstraint(
            "provider IN ('anthropic', 'openrouter', 'gemini')",
            name="ck_tenant_llm_config_provider",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_api_key: Mapped[str | None] = mapped_column(Text)
    configured_at: Mapped[dt.datetime] = _now_col(nullable=False)
    configured_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )
    tested_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
```

and after `LLM_TABLES`:

```python
# Team-wide LLM config, RLS-enabled by migration 0029.
TENANT_LLM_TABLES: tuple[str, ...] = ("tenant_llm_config",)
```

- [ ] **Step 4: Write the migration** `migrations/versions/0029_tenant_llm_config.py`:

```python
"""Add tenant_llm_config: team-wide LLM provider + encrypted API key

Revision ID: 0029_tenant_llm_config
Revises: 0028_finding_taxonomy_fields
Create Date: 2026-10-06

(Revision id kept <=32 chars for Postgres' ``alembic_version`` column; it is 22.)
"""

from __future__ import annotations

from alembic import op

from recon.db import models
from recon.db.base import Base

revision = "0029_tenant_llm_config"
down_revision = "0028_finding_taxonomy_fields"
branch_labels = None
depends_on = None

APP_ROLE = "recon_app"


def upgrade() -> None:
    bind = op.get_bind()
    # NOTE: 0001 runs create_all from the *live* models, so on a fresh DB / CI the table
    # already exists; create_all is idempotent (builds only what's missing), so this is a
    # no-op there and creates it on an existing DB. Same pattern as 0017 / 0026.
    Base.metadata.create_all(bind)
    # ENABLE is required (FORCE alone never activates RLS), and the policy must read
    # app.current_tenant: the GUC tenant_session() sets (recon.db.base).
    for table in models.TENANT_LLM_TABLES:
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
    op.execute('DROP POLICY IF EXISTS tenant_isolation ON "tenant_llm_config"')
    op.drop_table("tenant_llm_config")
```

- [ ] **Step 5: Run tests to verify they pass** — same path. Expected: all pass, including `test_rls_is_enabled_and_forced[tenant_llm_config]`. Check that the migrate container logs show `Running upgrade 0028_finding_taxonomy_fields -> 0029_tenant_llm_config` with no error (fresh DB).

- [ ] **Step 6: Commit**

```bash
git add apps/platform/src/recon/db/models.py apps/platform/src/recon/migrations/versions/0029_tenant_llm_config.py apps/platform/src/recon/db/llm_threat_model_rls_test.py
git commit -m "feat(db): add tenant_llm_config for a team-wide LLM key" -m "One row per tenant (UNIQUE tenant_id): provider, model, Fernet-encrypted key, configured_at/by, tested_at. Migration 0029 uses the idempotent 0017 pattern (create_all is a no-op on a fresh DB, where 0001 already built it) with RLS enabled+forced on app.current_tenant from day one. The RLS test now covers the new table.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `llm/crypto.py` + `llm/tenant_config.py` service

**Files:**
- Create: `apps/platform/src/recon/llm/crypto.py`
- Modify: `apps/platform/src/recon/llm/service.py` (delete `_encrypt`/`_decrypt`, import from crypto)
- Create: `apps/platform/src/recon/llm/tenant_config.py`
- Create: `apps/platform/src/recon/llm/tenant_config_test.py`

**Interfaces:**
- Consumes: `models.TenantLlmConfig`, `models.AppUser` (Task 2).
- Produces (all sync, run them via `run_in_threadpool` from async code):
  - `crypto.encrypt_api_key(plaintext: str) -> str`, `crypto.decrypt_api_key(ciphertext: str) -> str` (raises `crypto.KeyDecryptError`), `class KeyDecryptError(Exception)`
  - `tenant_config.get_config(tenant_id: str, *, include_actor: bool) -> dict | None`
  - `tenant_config.save_config(tenant_id: str, user_id: str, provider: str, model: str, api_key: str) -> dict | None` (None = caller isn't an admin in the DB)
  - `tenant_config.delete_config(tenant_id: str, user_id: str) -> bool | None` (None = not admin, False = nothing to delete)
  - `tenant_config.is_admin(tenant_id: str, user_id: str) -> bool`
  - `tenant_config.load_key(tenant_id: str) -> tuple[str, str, str] | None` (`(provider, model, api_key)`; raises `KeyDecryptError`)
  - `tenant_config.mark_tested(tenant_id: str) -> None`
  - Serialized dict keys: `provider, model, has_key, configured_at, configured_by, tested_at`.

- [ ] **Step 1: Write the failing test** — `llm/tenant_config_test.py`

```python
"""Team-wide LLM config service: admin-only writes (role checked in the DB), key never serialized."""

import uuid

import pytest

from recon.auth import service as auth_service
from recon.llm import tenant_config

pytestmark = pytest.mark.integration


def _seed_user(role: str) -> tuple[str, str]:
    tenant_id = str(uuid.uuid4())
    user_id = auth_service.seed_admin(
        username=f"u-{uuid.uuid4().hex[:8]}",
        password="pw",
        tenant_id=tenant_id,
        tenant_name="team-llm",
        role=role,
    )
    return tenant_id, user_id


def test_admin_round_trip_and_key_never_serialized():
    tenant, admin = _seed_user("admin")
    saved = tenant_config.save_config(tenant, admin, "openrouter", "m1", "secret-key-123")
    assert saved is not None
    assert saved["has_key"] is True and saved["provider"] == "openrouter"
    assert "secret-key-123" not in repr(saved)
    got = tenant_config.get_config(tenant, include_actor=True)
    assert got["configured_by"] is not None and "secret-key-123" not in repr(got)
    assert tenant_config.load_key(tenant) == ("openrouter", "m1", "secret-key-123")


def test_empty_key_keeps_the_stored_key():
    tenant, admin = _seed_user("admin")
    tenant_config.save_config(tenant, admin, "anthropic", "m1", "k1")
    tenant_config.save_config(tenant, admin, "anthropic", "m2", "")
    assert tenant_config.load_key(tenant) == ("anthropic", "m2", "k1")


def test_analyst_cannot_write():
    tenant, analyst = _seed_user("analyst")
    assert tenant_config.save_config(tenant, analyst, "anthropic", "m", "k") is None
    assert tenant_config.delete_config(tenant, analyst) is None
    assert tenant_config.get_config(tenant, include_actor=True) is None


def test_actor_hidden_without_include_actor():
    tenant, admin = _seed_user("admin")
    tenant_config.save_config(tenant, admin, "anthropic", "m", "k")
    assert tenant_config.get_config(tenant, include_actor=False)["configured_by"] is None


def test_delete_then_nothing():
    tenant, admin = _seed_user("admin")
    tenant_config.save_config(tenant, admin, "anthropic", "m", "k")
    assert tenant_config.delete_config(tenant, admin) is True
    assert tenant_config.delete_config(tenant, admin) is False
    assert tenant_config.load_key(tenant) is None


def test_mark_tested_stamps():
    tenant, admin = _seed_user("admin")
    tenant_config.save_config(tenant, admin, "anthropic", "m", "k")
    tenant_config.mark_tested(tenant)
    assert tenant_config.get_config(tenant, include_actor=False)["tested_at"] is not None
```

- [ ] **Step 2: Run to verify it fails** (integration; path `src/recon/llm/tenant_config_test.py`). Expected: `ModuleNotFoundError: No module named 'recon.llm.tenant_config'`.

- [ ] **Step 3: Create `llm/crypto.py`** (moved out of `service.py` so `tenant_config` and `service` can both use it without an import cycle; `service` imports `tenant_config`):

```python
"""API-key encryption for stored LLM keys (session and team).

Fernet with ``RECON_LLM_ENCRYPTION_KEY``. Empty key (dev default) stores the key in
cleartext; operators MUST set it in any real deployment."""

from __future__ import annotations

from recon.config import get_settings


class KeyDecryptError(Exception):
    """A stored key can't be decrypted (usually RECON_LLM_ENCRYPTION_KEY was rotated)."""


def encrypt_api_key(plaintext: str) -> str:
    key = get_settings().llm_encryption_key
    if not key:
        return plaintext  # dev mode: no encryption
    from cryptography.fernet import Fernet

    return Fernet(key.encode()).encrypt(plaintext.encode()).decode()


def decrypt_api_key(ciphertext: str) -> str:
    key = get_settings().llm_encryption_key
    if not key:
        return ciphertext  # dev mode
    from cryptography.fernet import Fernet, InvalidToken

    try:
        return Fernet(key.encode()).decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise KeyDecryptError("stored LLM key could not be decrypted") from exc
```

In `llm/service.py`: delete the `_encrypt` and `_decrypt` functions and their "Encryption helpers" banner, add `from recon.llm.crypto import decrypt_api_key, encrypt_api_key`, and replace every `_encrypt(` with `encrypt_api_key(` and `_decrypt(` with `decrypt_api_key(`. Remove the now-unused `from recon.config import get_settings` if ruff flags it.

- [ ] **Step 4: Create `llm/tenant_config.py`:**

```python
"""Team-wide (tenant-level) LLM config: one provider + model + key per tenant.

Writes re-check the caller's role against app_user inside the same tenant transaction:
the login token's role can be up to auth_token_ttl_seconds (8h) stale, and the user row
must exist for the configured_by FK."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from recon.db.base import tenant_session
from recon.db.models import AppUser, TenantLlmConfig
from recon.llm.crypto import decrypt_api_key, encrypt_api_key
from recon.observability import get_logger

log = get_logger("recon.llm.team_config")


def _is_admin(db: Session, user_id: str) -> bool:
    role = db.execute(
        select(AppUser.role).where(AppUser.id == uuid.UUID(user_id))
    ).scalar_one_or_none()
    return role == "admin"


def _row(db: Session, tenant_id: str) -> TenantLlmConfig | None:
    return db.execute(
        select(TenantLlmConfig).where(TenantLlmConfig.tenant_id == uuid.UUID(tenant_id))
    ).scalar_one_or_none()


def _serialize(db: Session, row: TenantLlmConfig, *, include_actor: bool) -> dict[str, Any]:
    email = None
    if include_actor and row.configured_by is not None:
        email = db.execute(
            select(AppUser.email).where(AppUser.id == row.configured_by)
        ).scalar_one_or_none()
    return {
        "provider": row.provider,
        "model": row.model,
        "has_key": bool(row.encrypted_api_key),
        "configured_at": row.configured_at.isoformat() if row.configured_at else None,
        "configured_by": email,
        "tested_at": row.tested_at.isoformat() if row.tested_at else None,
    }


def is_admin(tenant_id: str, user_id: str) -> bool:
    with tenant_session(tenant_id) as db:
        return _is_admin(db, user_id)


def get_config(tenant_id: str, *, include_actor: bool) -> dict[str, Any] | None:
    with tenant_session(tenant_id) as db:
        row = _row(db, tenant_id)
        return None if row is None else _serialize(db, row, include_actor=include_actor)


def save_config(
    tenant_id: str, user_id: str, provider: str, model: str, api_key: str
) -> dict[str, Any] | None:
    """Upsert the team config. None if the caller isn't an admin (per the DB)."""
    with tenant_session(tenant_id) as db:
        if not _is_admin(db, user_id):
            return None
        values: dict[str, Any] = {
            "provider": provider,
            "model": model,
            "configured_at": dt.datetime.now(dt.UTC),
            "configured_by": uuid.UUID(user_id),
            "tested_at": None,
        }
        if api_key:  # empty keeps the stored key, same contract as the session endpoint
            values["encrypted_api_key"] = encrypt_api_key(api_key)
        # ON CONFLICT so two concurrent first saves can't hit the UNIQUE(tenant_id).
        db.execute(
            insert(TenantLlmConfig)
            .values(tenant_id=uuid.UUID(tenant_id), **values)
            .on_conflict_do_update(index_elements=["tenant_id"], set_=values)
        )
        db.expire_all()
        result = _serialize(db, _row(db, tenant_id), include_actor=True)
    log.info(
        "llm.team_config.saved",
        tenant_id=tenant_id,
        user_id=user_id,
        provider=provider,
        model=model,
        key_changed=bool(api_key),
    )
    return result


def delete_config(tenant_id: str, user_id: str) -> bool | None:
    """True if deleted, False if there was nothing, None if the caller isn't an admin."""
    with tenant_session(tenant_id) as db:
        if not _is_admin(db, user_id):
            return None
        deleted = db.execute(
            delete(TenantLlmConfig).where(TenantLlmConfig.tenant_id == uuid.UUID(tenant_id))
        ).rowcount
    if deleted:
        log.info("llm.team_config.deleted", tenant_id=tenant_id, user_id=user_id)
    return bool(deleted)


def load_key(tenant_id: str) -> tuple[str, str, str] | None:
    """``(provider, model, api_key)`` if the team has a key. Raises KeyDecryptError."""
    with tenant_session(tenant_id) as db:
        row = _row(db, tenant_id)
        if row is None or not row.encrypted_api_key:
            return None
        return row.provider, row.model, decrypt_api_key(row.encrypted_api_key)


def mark_tested(tenant_id: str) -> None:
    with tenant_session(tenant_id) as db:
        db.execute(
            update(TenantLlmConfig)
            .where(TenantLlmConfig.tenant_id == uuid.UUID(tenant_id))
            .values(tested_at=dt.datetime.now(dt.UTC))
        )
```

- [ ] **Step 5: Run tests to verify they pass** — `src/recon/llm/tenant_config_test.py src/recon/llm/router_test.py src/recon/llm/service_test.py src/recon/db/llm_threat_model_rls_test.py`. Expected: all pass (`service_test` and the RLS test prove the crypto move didn't break session keys). Then `uv run ruff check src && uv run ruff format --check src && uv run mypy src/recon/findings src/recon/spec`.

- [ ] **Step 6: Commit**

```bash
git add apps/platform/src/recon/llm/
git commit -m "feat(llm): team-wide LLM config service with DB-checked admin writes" -m "tenant_config.py: get/save/delete/load_key/mark_tested for tenant_llm_config. Writes re-check the caller's role in app_user inside the same tenant transaction, because a token's role can be 8h stale; the user row must also exist for the configured_by FK. The upsert is INSERT ... ON CONFLICT (tenant_id) DO UPDATE, so concurrent first saves can't collide. An empty key keeps the stored one. The key is never serialized or logged; configured_by (an email) is only included on request.

Encryption moves to llm/crypto.py, which both modules import, so service.py can import tenant_config without a cycle. Decrypt failures raise KeyDecryptError instead of a bare InvalidToken.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Team key in `load_credentials`, decrypt-failure handling, clearer no-key error

**Files:**
- Modify: `apps/platform/src/recon/llm/service.py` (`load_credentials`)
- Modify: `apps/platform/src/recon/threat_model/service.py` (`run_generation`)
- Modify: `apps/platform/src/recon/llm/service_test.py`
- Create: `apps/platform/src/recon/threat_model/service_test.py`

**Interfaces:**
- Consumes: `tenant_config.load_key`, `crypto.KeyDecryptError` (Task 3).
- Produces: `load_credentials` order session → team → env → None.

- [ ] **Step 1: Write the failing tests.** Append to `llm/service_test.py`:

```python
from recon.db import models
from recon.db.base import tenant_session
from recon.llm.crypto import encrypt_api_key


def _save_team(tenant: str, provider: str, model: str, key: str) -> None:
    with tenant_session(tenant) as db:
        db.add(
            models.TenantLlmConfig(
                tenant_id=tenant, provider=provider, model=model,
                encrypted_api_key=encrypt_api_key(key),
            )
        )


def test_session_key_beats_team_key(session_ids):
    tenant, session_id = session_ids
    _save_team(tenant, "openrouter", "team-model", "team-key")
    llm_service.save_config(tenant, session_id, "anthropic", "claude-x", "session-key")
    assert llm_service.load_credentials(tenant, session_id) == ("anthropic", "claude-x", "session-key")


def test_team_key_beats_env_and_keeps_its_own_provider(session_ids, monkeypatch):
    tenant, session_id = session_ids
    # The session picked anthropic + a model but saved no key: the team key must NOT
    # borrow the session's provider or model.
    llm_service.save_config(tenant, session_id, "anthropic", "claude-x", "")
    _save_team(tenant, "openrouter", "team-model", "team-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    assert llm_service.load_credentials(tenant, session_id) == ("openrouter", "team-model", "team-key")


def test_env_used_when_no_team_key(session_ids, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    assert llm_service.load_credentials(*session_ids) == ("anthropic", None, "env-key")
```

Create `threat_model/service_test.py`:

```python
"""run_generation must fail loudly, never stay 'running', when the stored key can't be decrypted."""

import asyncio

import pytest

from recon.llm import service as llm_service
from recon.llm.crypto import KeyDecryptError
from recon.sessions import service as sessions_service
from recon.threat_model import service as tm_service

pytestmark = pytest.mark.integration


def _session(tenant: str) -> str:
    return sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    ).id


def test_undecryptable_key_marks_failed(tenant, monkeypatch):
    sid = _session(tenant)
    tm_service.trigger_generation(tenant, sid)
    monkeypatch.setattr(tm_service, "_assemble_context", lambda t, s: ("ctx", frozenset()))

    def _boom(t, s):
        raise KeyDecryptError("stored LLM key could not be decrypted")

    monkeypatch.setattr(llm_service, "load_credentials", _boom)
    asyncio.run(tm_service.run_generation(tenant, sid))
    state = tm_service.get_threat_model(tenant, sid)
    assert state["status"] == "failed"
    assert state["error"] == "stored LLM key could not be decrypted; re-save it"


def test_no_key_error_names_the_team_settings(tenant, monkeypatch):
    for env_var in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(env_var, raising=False)
    sid = _session(tenant)
    tm_service.trigger_generation(tenant, sid)
    monkeypatch.setattr(tm_service, "_assemble_context", lambda t, s: ("ctx", frozenset()))
    asyncio.run(tm_service.run_generation(tenant, sid))
    error = tm_service.get_threat_model(tenant, sid)["error"]
    assert error.startswith("no LLM API key")  # the UI's Settings link matches this prefix
    assert "ask an admin to set a team key in Settings" in error
```

- [ ] **Step 2: Run to verify they fail** — paths `src/recon/llm/service_test.py src/recon/threat_model/service_test.py`. Expected: `test_team_key_beats_env_and_keeps_its_own_provider` FAILS (gets the env key), `test_undecryptable_key_marks_failed` FAILS (status `running`), the error-text test FAILS.

- [ ] **Step 3: Implement.** In `llm/service.py` add `from recon.llm import tenant_config` to the imports, and in `load_credentials` insert the team step between the session block and the env loop:

```python
        saved_provider, saved_model = (row.provider, row.model) if row else (None, None)
    # Team key next: it carries its own provider + model, never the session's.
    team = tenant_config.load_key(tenant_id)
    if team is not None:
        return team
    for provider, env_var in _ENV_KEYS:
```

Update its docstring's middle sentence to: `A key saved on the session wins, then the team key (Settings), then an operator-wide env key, each with its own provider.`

In `threat_model/service.py`, add `from recon.llm.crypto import KeyDecryptError` and replace the credentials block in `run_generation` with:

```python
    try:
        credentials = llm_service.load_credentials(tenant_id, resolved)
    except Exception as exc:
        # NOTE: this runs after status=running; an escaped exception would leave the
        # model stuck there until the 5-minute orphan window, for every session of a
        # team with a bad team key.
        reason = (
            "stored LLM key could not be decrypted; re-save it"
            if isinstance(exc, KeyDecryptError)
            else "could not load LLM credentials"
        )
        _set_status(tenant_id, resolved, "failed", error=reason)
        log.error(
            "llm.credentials.decrypt_failed"
            if isinstance(exc, KeyDecryptError)
            else "llm.credentials.load_failed",
            session_id=resolved,
            error_type=type(exc).__name__,
        )
        return
    if credentials is None:
        _set_status(
            tenant_id,
            resolved,
            "failed",
            error="no LLM API key: save one for this session, ask an admin to set a team "
            "key in Settings, or set OPENROUTER_API_KEY / ANTHROPIC_API_KEY on the server",
        )
        return
    provider_name, model_name, api_key = credentials
```

- [ ] **Step 4: Run tests to verify they pass** — same paths plus `src/recon/db/llm_threat_model_rls_test.py`. Expected: all pass. Lint/format as before.

- [ ] **Step 5: Commit**

```bash
git add apps/platform/src/recon/llm/service.py apps/platform/src/recon/llm/service_test.py apps/platform/src/recon/threat_model/
git commit -m "feat(llm): use the team key between the session and env keys" -m "load_credentials order is now session key, then team key, then OPENROUTER_API_KEY / ANTHROPIC_API_KEY, then none. The team key returns its own provider and model, never the session's.

run_generation now catches credential-loading errors. Previously a KeyDecryptError (e.g. a rotated RECON_LLM_ENCRYPTION_KEY) escaped after status=running and left the threat model spinning for 5 minutes; with a team key that would hit every session in the team. It now fails immediately with 're-save it' and logs llm.credentials.decrypt_failed (no key material). The no-key error also points to the team key in Settings and keeps its 'no LLM API key' prefix for the UI link.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `/settings/llm` API with admin-only writes

**Files:**
- Modify: `apps/platform/src/recon/api/deps.py` (add `get_optional_principal`, `require_admin`; import `Depends`)
- Create: `apps/platform/src/recon/llm/settings_router.py`
- Modify: `apps/platform/src/recon/api/app.py` (mount once, after `llm_router`)
- Create: `apps/platform/src/recon/llm/settings_router_test.py`

**Interfaces:**
- Consumes: Task 3 service functions, Task 1 `ping_credentials`.
- Produces (HTTP):
  - `GET /settings/llm` → `{config: {provider, model, has_key, configured_at, configured_by, tested_at} | null, can_edit: bool, default_models: {provider: model}, providers: string[]}`
  - `PUT /settings/llm` body `{provider, model, api_key?}` → 200 config | 403 | 422
  - `DELETE /settings/llm` → 204 | 403 | 404
  - `POST /settings/llm/test` → 200 `{ok: true, provider, model}` | 400 `{detail}` | 403

- [ ] **Step 1: Write the failing test** — `llm/settings_router_test.py`

```python
"""/settings/llm: anyone in the team reads; only a current DB admin writes."""

import uuid

import pytest
from fastapi.testclient import TestClient

from recon.api.app import create_app
from recon.auth import service as auth_service
from recon.auth import token as auth_token
from recon.config import get_settings
from recon.llm import service as llm_service

pytestmark = pytest.mark.integration

AUTH_KEY = "settings-test-secret"


@pytest.fixture()
def client(monkeypatch):
    # Local copy of auth_router_test.make_auth_client: auth must be ON for role checks.
    monkeypatch.setenv("RECON_AUTH_SECRET", AUTH_KEY)
    get_settings.cache_clear()
    yield TestClient(create_app())
    get_settings.cache_clear()


def _user(tenant_id: str, role: str) -> str:
    return auth_service.seed_admin(
        username=f"u-{uuid.uuid4().hex[:8]}", password="pw",
        tenant_id=tenant_id, tenant_name="settings", role=role,
    )


def _auth(tenant_id: str, user_id: str, role: str) -> dict:
    token = auth_token.mint(
        user_id=user_id, tenant_id=tenant_id, role=role, key=AUTH_KEY, ttl_seconds=600
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def team():
    tenant_id = str(uuid.uuid4())
    admin = _user(tenant_id, "admin")
    analyst = _user(tenant_id, "analyst")
    return tenant_id, _auth(tenant_id, admin, "admin"), _auth(tenant_id, analyst, "analyst")


def test_admin_saves_analyst_reads_without_key(client, team):
    tenant_id, admin_h, analyst_h = team
    r = client.put("/settings/llm", json={"provider": "openrouter", "model": "m", "api_key": "sk-x"}, headers=admin_h)
    assert r.status_code == 200, r.text
    assert r.json()["has_key"] is True
    r = client.get("/settings/llm", headers=analyst_h)
    body = r.json()
    assert r.status_code == 200
    assert body["can_edit"] is False
    assert body["config"]["provider"] == "openrouter"
    assert "sk-x" not in r.text
    assert set(body["providers"]) == {"anthropic", "openrouter", "gemini"}
    assert client.get("/settings/llm", headers=admin_h).json()["can_edit"] is True


def test_analyst_write_is_403(client, team):
    _, _, analyst_h = team
    r = client.put("/settings/llm", json={"provider": "anthropic", "model": "m", "api_key": "k"}, headers=analyst_h)
    assert r.status_code == 403


def test_demoted_admin_token_is_403(client):
    # The token still says admin, but the DB says analyst: the DB wins.
    tenant_id = str(uuid.uuid4())
    user_id = _user(tenant_id, "analyst")
    r = client.put(
        "/settings/llm",
        json={"provider": "anthropic", "model": "m", "api_key": "k"},
        headers=_auth(tenant_id, user_id, "admin"),
    )
    assert r.status_code == 403


def test_bad_provider_is_422(client, team):
    _, admin_h, _ = team
    r = client.put("/settings/llm", json={"provider": "nope", "model": "m"}, headers=admin_h)
    assert r.status_code == 422


def test_delete_then_404(client, team):
    _, admin_h, _ = team
    client.put("/settings/llm", json={"provider": "anthropic", "model": "m", "api_key": "k"}, headers=admin_h)
    assert client.delete("/settings/llm", headers=admin_h).status_code == 204
    assert client.delete("/settings/llm", headers=admin_h).status_code == 404


def test_test_endpoint_reaches_ping(client, team, monkeypatch):
    _, admin_h, _ = team

    class _Stub:
        async def generate_structured(self, **_kw):
            return None

    monkeypatch.setattr(llm_service, "build_provider", lambda provider, api_key, model=None: _Stub())
    client.put("/settings/llm", json={"provider": "anthropic", "model": "m", "api_key": "k"}, headers=admin_h)
    r = client.post("/settings/llm/test", headers=admin_h)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "provider": "anthropic", "model": "m"}
    assert client.get("/settings/llm", headers=admin_h).json()["config"]["tested_at"] is not None
```

- [ ] **Step 2: Run to verify it fails** — path `src/recon/llm/settings_router_test.py`. Expected: 404s (route missing).

- [ ] **Step 3: Implement deps.** In `api/deps.py` change `from fastapi import Header, HTTPException` to `from fastapi import Depends, Header, HTTPException`, and add after `get_principal`:

```python
def get_optional_principal(authorization: str | None = Header(default=None)) -> Principal | None:
    """The authenticated identity, or None (auth off / no or bad token). For routes
    that serve everyone but tailor the response to the caller, like can_edit."""
    claims = _bearer_claims(authorization, get_settings())
    if claims is None:
        return None
    return Principal(user_id=claims.user_id, tenant_id=claims.tenant_id, role=claims.role)


def require_admin(principal: Principal = Depends(get_principal)) -> Principal:
    """403 unless the token says admin. A fast gate only: services re-check the role in
    the DB, because a token's role can be up to auth_token_ttl_seconds stale."""
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    return principal
```

- [ ] **Step 4: Create `llm/settings_router.py`:**

```python
"""Team-wide LLM settings: GET/PUT/DELETE /settings/llm + POST /settings/llm/test.

Thin: validate, delegate to llm.tenant_config, map results to HTTP. Reads are open to
the whole team; writes need an admin (token gate here, current DB role in the service)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from recon.api.deps import get_optional_principal, get_tenant_id, require_admin
from recon.auth.service import Principal
from recon.llm import service as llm_service
from recon.llm import tenant_config
from recon.llm.crypto import KeyDecryptError
from recon.llm.provider import DEFAULT_MODELS, VALID_PROVIDERS
from recon.observability import get_logger

router = APIRouter(tags=["settings"])
log = get_logger("recon.llm.team_config")

_NOT_ADMIN = "admin role required"


class TeamLlmConfigIn(BaseModel):
    provider: str
    model: str
    api_key: str = ""  # empty keeps the stored key


@router.get("/settings/llm")
async def get_team_llm_settings(
    tenant_id: str = Depends(get_tenant_id),
    principal: Principal | None = Depends(get_optional_principal),
) -> dict:
    # configured_by (an email) only with a real login, so a header-only read
    # (RECON_ALLOW_HEADER_TENANT) never exposes an admin's address.
    config = await run_in_threadpool(
        tenant_config.get_config, tenant_id, include_actor=principal is not None
    )
    return {
        "config": config,
        "can_edit": principal is not None and principal.role == "admin",
        "default_models": DEFAULT_MODELS,
        "providers": sorted(VALID_PROVIDERS),
    }


@router.put("/settings/llm")
async def save_team_llm_settings(
    body: TeamLlmConfigIn, principal: Principal = Depends(require_admin)
) -> dict:
    if body.provider not in VALID_PROVIDERS:
        raise HTTPException(
            status_code=422, detail=f"provider must be one of {sorted(VALID_PROVIDERS)}"
        )
    if not body.model.strip():
        raise HTTPException(status_code=422, detail="model must not be empty")
    result = await run_in_threadpool(
        tenant_config.save_config,
        principal.tenant_id,
        principal.user_id,
        body.provider,
        body.model.strip(),
        body.api_key.strip(),
    )
    if result is None:
        raise HTTPException(status_code=403, detail=_NOT_ADMIN)
    return result


@router.delete("/settings/llm", status_code=204)
async def delete_team_llm_settings(principal: Principal = Depends(require_admin)) -> Response:
    deleted = await run_in_threadpool(
        tenant_config.delete_config, principal.tenant_id, principal.user_id
    )
    if deleted is None:
        raise HTTPException(status_code=403, detail=_NOT_ADMIN)
    if not deleted:
        raise HTTPException(status_code=404, detail="no team LLM config")
    return Response(status_code=204)


@router.post("/settings/llm/test")
async def test_team_llm_settings(principal: Principal = Depends(require_admin)) -> dict:
    if not await run_in_threadpool(tenant_config.is_admin, principal.tenant_id, principal.user_id):
        raise HTTPException(status_code=403, detail=_NOT_ADMIN)
    try:
        credentials = await run_in_threadpool(tenant_config.load_key, principal.tenant_id)
    except KeyDecryptError as exc:
        raise HTTPException(
            status_code=400, detail="stored key could not be decrypted; re-save it"
        ) from exc
    if credentials is None:
        raise HTTPException(status_code=400, detail="no team key saved")
    provider_name, model, api_key = credentials
    # Awaited on the request loop (see llm.service.ping_credentials).
    error = await llm_service.ping_credentials(provider_name, model, api_key)
    log.info(
        "llm.team_config.tested",
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        provider=provider_name,
        ok=error is None,
    )
    if error is not None:
        raise HTTPException(status_code=400, detail=error)
    await run_in_threadpool(tenant_config.mark_tested, principal.tenant_id)
    return {"ok": True, "provider": provider_name, "model": model}
```

- [ ] **Step 5: Mount it** in `api/app.py`: add `from recon.llm import settings_router as llm_settings_router` next to the `llm_router` import, and after `app.include_router(llm_router.router)` (line ~74) add:

```python
    # Mounted once (no /api twin): the extension doesn't use team settings.
    app.include_router(llm_settings_router.router)
```

- [ ] **Step 6: Run tests to verify they pass** — path `src/recon/llm/settings_router_test.py`. Expected: 6 passed. Lint/format.

- [ ] **Step 7: Commit**

```bash
git add apps/platform/src/recon/api/deps.py apps/platform/src/recon/api/app.py apps/platform/src/recon/llm/settings_router.py apps/platform/src/recon/llm/settings_router_test.py
git commit -m "feat(api): /settings/llm team LLM settings with admin-only writes" -m "GET is open to the team and returns the config (never the key), can_edit, the per-provider default models and the provider list. PUT/DELETE/test need an admin: require_admin is a fast token gate, and the service re-checks the current role in app_user, so a demoted admin's still-valid token gets a 403. This is the repo's first role-enforced route.

configured_by (an email) is only returned with a real login. /test awaits the ping on the request loop. Mounted once, not under /api.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Settings page, sidebar item, route, dev proxy

**Files:**
- Modify: `apps/platform/web/src/api/apiClient.ts` (export `request` and `json`)
- Create: `apps/platform/web/src/features/settings/settingsApi.ts`
- Create: `apps/platform/web/src/features/settings/SettingsPage.tsx`
- Create: `apps/platform/web/src/features/settings/SettingsView.tsx`
- Create: `apps/platform/web/src/features/settings/settings.css`
- Create: `apps/platform/web/src/features/settings/SettingsPage.test.tsx`
- Modify: `apps/platform/web/src/shell/Shell.tsx:31`, `shell/TopBar.tsx:81`, `shell/Sidebar.tsx:47` + Sessions button block, `shell/icons.tsx` (`gear`)
- Modify: `apps/platform/web/src/main.tsx` (route), `apps/platform/web/vite.config.ts` (proxy)

**Interfaces:**
- Consumes: Task 5 HTTP API.
- Produces: route `/settings`; `TeamLlmSettings`, `TeamLlmConfig` types.

- [ ] **Step 1: Write the failing test** — `features/settings/SettingsPage.test.tsx`

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SettingsPage } from "./SettingsPage";
import * as api from "./settingsApi";
import type { TeamLlmSettings } from "./settingsApi";

const BASE: TeamLlmSettings = {
  config: null,
  can_edit: true,
  default_models: { anthropic: "claude-default", openrouter: "or-default", gemini: "gem-default" },
  providers: ["anthropic", "gemini", "openrouter"],
};
const SAVED: TeamLlmSettings = {
  ...BASE,
  config: {
    provider: "openrouter", model: "or-model", has_key: true,
    configured_at: "2026-10-06T00:00:00Z", configured_by: "admin@acme.io", tested_at: null,
  },
};

beforeEach(() => { vi.restoreAllMocks(); });

describe("SettingsPage", () => {
  it("admin saves the typed key, and the key field is empty again afterwards", async () => {
    vi.spyOn(api, "getTeamLlmSettings").mockResolvedValueOnce(BASE).mockResolvedValueOnce(SAVED);
    const save = vi.spyOn(api, "saveTeamLlmSettings").mockResolvedValue(SAVED.config!);
    render(<SettingsPage tenantId="t1" />);
    const key = await screen.findByLabelText("API key");
    expect(screen.getByLabelText("Model")).toHaveValue("claude-default");
    await userEvent.type(key, "sk-typed");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(save).toHaveBeenCalledWith("t1", { provider: "anthropic", model: "claude-default", api_key: "sk-typed" });
    expect(await screen.findByRole("status")).toHaveTextContent("Saved");
    expect(screen.getByLabelText("API key")).toHaveValue("");
  });

  it("switching provider swaps in that provider's default model", async () => {
    vi.spyOn(api, "getTeamLlmSettings").mockResolvedValue(BASE);
    render(<SettingsPage tenantId="t1" />);
    await userEvent.selectOptions(await screen.findByLabelText("Provider"), "openrouter");
    expect(screen.getByLabelText("Model")).toHaveValue("or-default");
  });

  it("a saved key is never rendered, only flagged", async () => {
    vi.spyOn(api, "getTeamLlmSettings").mockResolvedValue(SAVED);
    render(<SettingsPage tenantId="t1" />);
    const key = await screen.findByLabelText("API key");
    expect(key).toHaveValue("");
    expect(key).toHaveAttribute("placeholder", "A key is saved. Leave blank to keep it.");
    expect(screen.getByText(/admin@acme\.io/)).toBeInTheDocument();
  });

  it("analyst sees a read-only view with no key field", async () => {
    vi.spyOn(api, "getTeamLlmSettings").mockResolvedValue({ ...SAVED, can_edit: false });
    render(<SettingsPage tenantId="t1" />);
    expect(await screen.findByText("Only admins can change the team key.")).toBeInTheDocument();
    expect(screen.getByText("or-model")).toBeInTheDocument();
    expect(screen.queryByLabelText("API key")).toBeNull();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify it fails** — `npm test -- --run src/features/settings`. Expected: FAIL, cannot resolve `./SettingsPage`.

- [ ] **Step 3: Export the request helpers.** In `web/src/api/apiClient.ts`, change `async function request<T>(` to `export async function request<T>(` and `function json(` to `export function json(` (feature-local API files reuse them; apiClient.ts is already over the ~300-line cap, so new calls don't go there).

- [ ] **Step 4: Create `features/settings/settingsApi.ts`:**

```ts
import { json, request } from "../../api/apiClient";

export interface TeamLlmConfig {
  provider: string;
  model: string;
  has_key: boolean;
  configured_at: string | null;
  configured_by: string | null;
  tested_at: string | null;
}

export interface TeamLlmSettings {
  config: TeamLlmConfig | null;
  can_edit: boolean;
  default_models: Record<string, string>;
  providers: string[];
}

export function getTeamLlmSettings(tenantId: string): Promise<TeamLlmSettings> {
  return request("/settings/llm", {}, tenantId);
}

export function saveTeamLlmSettings(
  tenantId: string,
  body: { provider: string; model: string; api_key: string },
): Promise<TeamLlmConfig> {
  return request("/settings/llm", json("PUT", body), tenantId);
}

export function deleteTeamLlmSettings(tenantId: string): Promise<void> {
  return request("/settings/llm", { method: "DELETE" }, tenantId);
}

export function testTeamLlmSettings(tenantId: string): Promise<{ ok: boolean; provider: string; model: string }> {
  return request("/settings/llm/test", { method: "POST" }, tenantId);
}
```

- [ ] **Step 5: Create `features/settings/SettingsPage.tsx`:**

```tsx
import { useCallback, useEffect, useState } from "react";
import { ConfirmModal } from "../../shell/ConfirmModal";
import {
  deleteTeamLlmSettings, getTeamLlmSettings, saveTeamLlmSettings, testTeamLlmSettings,
  type TeamLlmSettings,
} from "./settingsApi";
import "./settings.css";

type Status = { kind: "ok" | "error"; text: string };

const when = (iso: string) => new Date(iso).toLocaleString();

export function SettingsPage({ tenantId }: { tenantId: string }) {
  const [settings, setSettings] = useState<TeamLlmSettings | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);

  const reload = useCallback(async () => {
    const next = await getTeamLlmSettings(tenantId);
    const chosen = next.config?.provider ?? next.providers[0] ?? "";
    setSettings(next);
    setProvider(chosen);
    setModel(next.config?.model ?? next.default_models[chosen] ?? "");
    setApiKey(""); // never keep a key in the DOM after a round-trip
  }, [tenantId]);

  useEffect(() => {
    reload().catch((e: unknown) => setLoadError(e instanceof Error ? e.message : String(e)));
  }, [reload]);

  async function run(action: () => Promise<unknown>, okText: string) {
    setBusy(true);
    setStatus(null);
    try {
      await action();
      await reload();
      setStatus({ kind: "ok", text: okText });
    } catch (e) {
      setStatus({ kind: "error", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  }

  if (loadError) return <section className="card settings-card"><p className="settings-error">{loadError}</p></section>;
  if (!settings) return <section className="card settings-card"><p>Loading…</p></section>;

  const cfg = settings.config;
  return (
    <section className="card settings-card" aria-labelledby="team-llm-title">
      <h2 id="team-llm-title" className="rp-title">Team LLM provider</h2>
      <p className="settings-hint">
        Used for threat models in every session of this team. A key saved on a session overrides it.
      </p>

      {settings.can_edit ? (
        <form
          className="settings-form"
          onSubmit={(e) => {
            e.preventDefault();
            void run(() => saveTeamLlmSettings(tenantId, { provider, model: model.trim(), api_key: apiKey }), "Saved");
          }}
        >
          <label>
            Provider
            <select
              value={provider}
              onChange={(e) => { setProvider(e.target.value); setModel(settings.default_models[e.target.value] ?? ""); }}
            >
              {settings.providers.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          <label>
            Model
            <input value={model} onChange={(e) => setModel(e.target.value)} />
          </label>
          <label>
            API key
            <input
              type="password"
              autoComplete="off"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={cfg?.has_key ? "A key is saved. Leave blank to keep it." : "Paste your API key"}
            />
          </label>
          <div className="settings-actions">
            <button type="submit" className="btn-primary" disabled={busy || !model.trim() || (!cfg?.has_key && !apiKey)}>
              Save
            </button>
            <button type="button" className="shell-btn" disabled={busy || !cfg?.has_key}
              onClick={() => void run(() => testTeamLlmSettings(tenantId), "Key works")}>
              Test
            </button>
            <button type="button" className="shell-btn" disabled={busy || !cfg}
              onClick={() => setConfirmRemove(true)}>
              Remove
            </button>
          </div>
        </form>
      ) : (
        <>
          <dl className="settings-readonly">
            <dt>Provider</dt><dd>{cfg?.provider ?? "Not set"}</dd>
            <dt>Model</dt><dd>{cfg?.model ?? "—"}</dd>
            <dt>Key</dt><dd>{cfg?.has_key ? "Saved" : "Not set"}</dd>
          </dl>
          <p className="settings-hint">Only admins can change the team key.</p>
        </>
      )}

      {cfg && (
        <p className="settings-meta">
          Last saved {cfg.configured_at ? when(cfg.configured_at) : "—"}
          {cfg.configured_by ? ` by ${cfg.configured_by}` : ""}
          {cfg.tested_at ? ` · tested ${when(cfg.tested_at)}` : ""}
        </p>
      )}
      {status && <p role="status" className={status.kind === "ok" ? "settings-ok" : "settings-error"}>{status.text}</p>}

      {confirmRemove && (
        <ConfirmModal
          title="Remove the team LLM key?"
          message="Sessions without their own key will stop generating threat models."
          confirmLabel="Remove"
          danger
          onConfirm={() => { setConfirmRemove(false); void run(() => deleteTeamLlmSettings(tenantId), "Removed"); }}
          onCancel={() => setConfirmRemove(false)}
        />
      )}
    </section>
  );
}
```

- [ ] **Step 6: Create `features/settings/settings.css`:**

```css
.settings-card { max-width: 560px; }
.settings-hint, .settings-meta { color: var(--text-2, #9aa3b5); font-size: 13px; margin: 6px 0 14px; }
.settings-form { display: grid; gap: 12px; }
.settings-form label { display: grid; gap: 4px; font-size: 13px; }
.settings-actions { display: flex; gap: 8px; margin-top: 4px; }
.settings-readonly { display: grid; grid-template-columns: max-content 1fr; gap: 6px 16px; margin: 0 0 8px; }
.settings-readonly dd { margin: 0; overflow-wrap: anywhere; }
.settings-ok { color: var(--lime, #cdeb45); }
.settings-error { color: var(--danger, #f0716b); }
```

- [ ] **Step 7: Create `features/settings/SettingsView.tsx`:**

```tsx
import { Shell } from "../../shell/Shell";
import { useTenant } from "../../tenant/TenantContext";
import { SettingsPage } from "./SettingsPage";

// The /settings route: the shell in "settings" mode (no active run), like /sessions.
export function SettingsView() {
  const { tenantId } = useTenant();
  return (
    <Shell mode="settings">
      {tenantId && <SettingsPage tenantId={tenantId} />}
    </Shell>
  );
}
```

- [ ] **Step 8: Wire the shell.**
  - `shell/Shell.tsx:31` and `shell/TopBar.tsx:81`: `mode?: "run" | "sessions";` → `mode?: "run" | "sessions" | "settings";`
  - `shell/Sidebar.tsx:47`: `{ mode: "run" | "sessions"; runId?: string }` → `{ mode: "run" | "sessions" | "settings"; runId?: string }`
  - `shell/Sidebar.tsx`: right after the Sessions `</button>` (before `</nav>`), add:

```tsx
        <button
          type="button"
          className={"shell-nav-item" + (mode === "settings" ? " is-active" : "")}
          aria-current={mode === "settings" ? "page" : undefined}
          onClick={() => navigate("/settings")}
        >
          <span className="shell-nav-ico"><Icon name="gear" /></span>
          <span className="shell-nav-txt">Settings</span>
        </button>
```

  - `shell/icons.tsx`, in `PATHS` after `x`:

```tsx
  // Settings (cog).
  gear: (<><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></>),
```

  - `main.tsx`: add `import { SettingsView } from "./features/settings/SettingsView";` and the route `{ path: "/settings", Component: SettingsView },` after `/sessions`.
  - `vite.config.ts` proxy: add `"/settings/llm": "http://localhost:8000",` with the comment `// Not "/settings": that's also the SPA route; a hard refresh must stay in Vite.`

- [ ] **Step 9: Run tests to verify they pass** — `npm test -- --run` (all 44+ files), `npm run lint`, `npm run build`. Expected: all green; the 4 new tests pass.

- [ ] **Step 10: Commit**

```bash
git add apps/platform/web/
git commit -m "feat(web): Settings page for the team LLM key" -m "The workspace had a Threat Model tab but nowhere to set an LLM key; only the extension popup could. A new Settings sidebar item (/settings, its own shell mode) shows a Team LLM provider card. Admins get provider, model (pre-filled with the provider's default), a write-only key field, and Save / Test / Remove (in-app confirm). Analysts get a read-only view. The key is never rendered: the field is always empty after load and only shows 'A key is saved'.

apiClient's request/json are exported so the feature keeps its calls in settingsApi.ts (apiClient.ts is already over the line cap). The Vite dev proxy forwards /settings/llm only, so a hard refresh of /settings stays in the SPA.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Link the Threat Model no-key error to Settings

**Files:**
- Modify: `apps/platform/web/src/features/threat-model/ThreatModelPage.tsx:184-186`
- Create: `apps/platform/web/src/features/threat-model/ThreatModelPage.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { ThreatModelPage } from "./ThreatModelPage";
import * as api from "../../api/apiClient";
import type { ThreatModelResponse } from "../../api/types";

vi.mock("../../tenant/TenantContext", () => ({ useTenant: () => ({ tenantId: "t1" }) }));

const failed = (error: string): ThreatModelResponse => ({
  status: "failed", provider: null, model: null, prompt_tokens: null, completion_tokens: null,
  analysis_summary: null, error, generated_at: null, updated_at: null,
});

beforeEach(() => { vi.restoreAllMocks(); });

describe("ThreatModelPage", () => {
  it("links the no-key failure to Settings", async () => {
    vi.spyOn(api, "getThreatModel").mockResolvedValue(failed("no LLM API key: save one for this session, ..."));
    render(<MemoryRouter><ThreatModelPage sessionId="s1" /></MemoryRouter>);
    const link = await screen.findByRole("link", { name: "Set a team key in Settings →" });
    expect(link).toHaveAttribute("href", "/settings");
  });

  it("no Settings link for other failures", async () => {
    vi.spyOn(api, "getThreatModel").mockResolvedValue(failed("provider timeout"));
    render(<MemoryRouter><ThreatModelPage sessionId="s1" /></MemoryRouter>);
    expect(await screen.findByText(/provider timeout/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Settings/ })).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify it fails** — `npm test -- --run src/features/threat-model`. Expected: first test FAILS (no link).

- [ ] **Step 3: Implement.** In `ThreatModelPage.tsx` add `import { Link } from "react-router";` and replace the failed-status paragraph with:

```tsx
      {data?.status === "failed" && (
        <p className="tm-error">
          Generation failed: {data.error ?? "unknown error"}
          {data.error?.startsWith("no LLM API key") && (
            <> <Link to="/settings">Set a team key in Settings →</Link></>
          )}
        </p>
      )}
```

- [ ] **Step 4: Run tests to verify they pass** — `npm test -- --run`, `npm run lint`.

- [ ] **Step 5: Commit**

```bash
git add apps/platform/web/src/features/threat-model/
git commit -m "feat(web): link the threat model no-key error to Settings" -m "The 'no LLM API key' failure now ends with a 'Set a team key in Settings' link. It's matched on the error's stable prefix, so other failures don't get it.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Docs, full verification, code review

**Files:**
- Modify: `docs/OPERATING.md` (new short section near the threat model / LLM content; find it with `grep -n "Threat Model\|LLM" docs/OPERATING.md`)

- [ ] **Step 1: Add the docs section:**

```markdown
### LLM key for threat models

Threat-model generation uses the first key it finds, each with its own provider:

1. a key saved on the **session** (browser extension → Settings)
2. the **team key**: workspace sidebar → **Settings** (admins only)
3. the server's `OPENROUTER_API_KEY`, then `ANTHROPIC_API_KEY` (set in the untracked
   `docker-compose.override.yml` under `api:`). Every team without its own key spends
   this one, so leave both unset on multi-tenant deployments.

Keys are encrypted with `RECON_LLM_ENCRYPTION_KEY` (stored in cleartext if it's empty:
dev only). After rotating that key, re-save the stored keys; a key that can't be
decrypted fails the threat model with "re-save it".
```

- [ ] **Step 2: Run all four CI lanes** (DoS timing guards only while Docker is idle):
  - host-tests (from `apps/platform`): `uv run ruff check src && uv run ruff format --check src && uv run mypy src/recon/findings src/recon/spec && RECON_REQUIRE_ENGINES=1 uv run pytest -m "not integration" --cov=recon --cov-fail-under=60`
  - frontend (from `apps/platform/web`): `npm ci && npm run lint && npm test -- --run && npm run build`
  - extension (from `apps/capture/chrome-extension`): `npm ci && npm run build && for t in tests/test_*.mjs; do node "$t" || exit 1; done`, then `git checkout -- dist`
  - integration: the full suite in the isolated compose project, `pytest -m 'not dos_timing'`, on an empty DB.
  Expected: all green, 0 failed.

- [ ] **Step 3: Higher-model code review** (CLAUDE.md §4 gate 2): dispatch a review subagent over `git diff feat/llm-env-key-fallback..HEAD`; fix confirmed findings, re-run the affected lanes.

- [ ] **Step 4: Visual walkthrough** (CLAUDE.md §2): rebuild the local stack from this branch; as admin, open Settings, save, test, remove; trigger the threat-model no-key error and follow the link. Screenshot.

- [ ] **Step 5: Commit the docs**

```bash
git add docs/OPERATING.md
git commit -m "docs(operating): where the threat-model LLM key comes from" -m "Documents the lookup order (session key, team key in Settings, server env keys), the multi-tenant caveat for env keys, and re-saving keys after rotating RECON_LLM_ENCRYPTION_KEY.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
