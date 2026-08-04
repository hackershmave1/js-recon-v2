"""slice-2 finding + finding_occurrence tables + row-level security

Revision ID: 0002_findings
Revises: 0001_initial
Create Date: 2026-07-20

The REQ-D3 finding-identity store. Tables are built from the model metadata
(``create_all`` is idempotent — it only creates what's missing), then FORCE
row-level security + the ``tenant_isolation`` policy are layered on the two new
tables (REQ-S1), mirroring 0001. Grants are explicit so the app role can use the
tables even where default privileges aren't in effect.
"""

from __future__ import annotations

from alembic import op

from recon.db import models
from recon.db.base import Base

revision = "0002_findings"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

APP_ROLE = "recon_app"


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind)  # idempotent: builds only the new tables

    for table in models.FINDINGS_TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        # DROP-then-CREATE keeps the migration idempotent (CREATE POLICY has no
        # IF NOT EXISTS in Postgres).
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"')
        op.execute(
            f'CREATE POLICY tenant_isolation ON "{table}" '
            "USING (tenant_id::text = current_setting('app.current_tenant', true)) "
            "WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true))"
        )
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table}" TO {APP_ROLE}')


def downgrade() -> None:
    for table in models.FINDINGS_TABLES:
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"')
    op.drop_table("finding_occurrence")  # child first (FK -> finding)
    op.drop_table("finding")
