"""tech-detection run_technology table + RLS

Revision ID: 0017_technology
Revises: 0016_finding_type_generic
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

revision = "0017_technology"
down_revision = "0016_finding_type_generic"
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
