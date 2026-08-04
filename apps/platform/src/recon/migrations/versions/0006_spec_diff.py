"""shadow-API spec-diff tables (session_spec, finding_spec_status) + RLS

Revision ID: 0006_spec_diff
Revises: 0005_run_asset
Create Date: 2026-07-28

Spec-diff store (REQ §6.1, §6.2 / gate B6). Mirrors 0004: both tables are built
from the live model metadata (``create_all`` is idempotent — only what's
missing), then FORCE row-level security + the ``tenant_isolation`` policy + an
explicit GRANT are layered on per table (REQ-S1). Both tables are brand new (no
existing-table column adds like 0005's ``finding_occurrence.run_asset_id``), so
the create_all-vs-incremental seam that bit 0003 does not apply here.
"""

from __future__ import annotations

from alembic import op

from recon.db import models
from recon.db.base import Base

revision = "0006_spec_diff"
down_revision = "0005_run_asset"
branch_labels = None
depends_on = None

APP_ROLE = "recon_app"


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind)  # idempotent: builds only the two new tables

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
