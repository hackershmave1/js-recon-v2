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
