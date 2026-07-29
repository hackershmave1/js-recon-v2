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
