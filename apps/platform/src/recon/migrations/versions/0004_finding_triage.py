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
