"""R6 engagement tier: engagement table (+RLS) and session.engagement_id/archived_at

Revision ID: 0009_engagement
Revises: 0008_session_wrapper
Create Date: 2026-08-02

Mirrors 0008 for the brand-new table: build it from live model metadata
(``create_all`` is idempotent — only what's missing), then FORCE row-level
security + the tenant_isolation policy + an explicit GRANT (REQ-S1). ``create_all``
does NOT alter existing tables, so the two new columns on the already-RLS'd
``session`` table are added explicitly, guarded with ``IF NOT EXISTS`` (0003's
fresh-DB trap: a from-scratch ``create_all`` already builds ``session`` WITH them,
so a plain ``add_column`` fails DuplicateColumn on a fresh DB / CI). The session FK
is deliberately SET NULL, not CASCADE, so deleting an engagement never destroys a
session's recon history.
"""

from __future__ import annotations

from alembic import op

from recon.db import models
from recon.db.base import Base

revision = "0009_engagement"
down_revision = "0008_session_wrapper"
branch_labels = None
depends_on = None

APP_ROLE = "recon_app"


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind)  # idempotent: builds only the new engagement table

    for table in models.ENGAGEMENT_TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"')
        op.execute(
            f'CREATE POLICY tenant_isolation ON "{table}" '
            "USING (tenant_id::text = current_setting('app.current_tenant', true)) "
            "WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true))"
        )
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table}" TO {APP_ROLE}')

    # The session table already exists (RLS'd since 0001); create_all won't add
    # columns to it, so add the engagement FK + archive marker explicitly —
    # guarded with IF NOT EXISTS because a from-scratch ``upgrade head`` already
    # built ``session`` WITH these columns via 0001's create_all (see 0003), so a
    # plain add_column would fail DuplicateColumn on a fresh DB / CI.
    op.execute(
        "ALTER TABLE session ADD COLUMN IF NOT EXISTS engagement_id UUID "
        "REFERENCES engagement(id) ON DELETE SET NULL"
    )
    op.execute("ALTER TABLE session ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE session DROP COLUMN IF EXISTS archived_at")
    op.execute("ALTER TABLE session DROP COLUMN IF EXISTS engagement_id")
    for table in models.ENGAGEMENT_TABLES:
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"')
    op.execute("DROP TABLE IF EXISTS engagement")
