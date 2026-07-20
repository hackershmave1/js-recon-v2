"""Engine, session factory, and the tenant-scoped session (REQ-S1).

Isolation is enforced in the database, not just the API: every tenant-scoped
table has a row-level-security policy keyed on ``current_setting('app.current_tenant')``.
The only supported way to touch those tables is :func:`tenant_session`, which
sets that GUC for the life of one transaction. Forget it and RLS returns nothing.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from recon.config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()

# App engine: the non-superuser role, subject to RLS. All tenant work goes here.
engine = create_engine(_settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)

# Admin engine: the owning role, for migrations/bootstrap/health only.
admin_engine = create_engine(
    _settings.database_admin_url, pool_pre_ping=True, future=True
)
AdminSessionLocal = sessionmaker(
    bind=admin_engine, expire_on_commit=False, future=True
)


@contextmanager
def tenant_session(tenant_id: str) -> Iterator[Session]:
    """A transaction scoped to one tenant.

    ``SET LOCAL`` binds the tenant id for this transaction only, so RLS policies
    filter every read and write to that tenant. Commits on success, rolls back
    on error.
    """
    session = SessionLocal()
    try:
        with session.begin():
            # set_config(..., is_local=true) takes a bind parameter (SET LOCAL
            # cannot), so this stays correct under psycopg3/asyncpg too.
            session.execute(
                text("SELECT set_config('app.current_tenant', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            yield session
    finally:
        session.close()


@contextmanager
def admin_session() -> Iterator[Session]:
    """A session on the owning role — for bootstrap (create tenant), health
    checks, and cross-tenant ops. It bypasses RLS, so never expose it to a
    request handler; tenant work must go through :func:`tenant_session`."""
    session = AdminSessionLocal()
    try:
        with session.begin():
            yield session
    finally:
        session.close()
