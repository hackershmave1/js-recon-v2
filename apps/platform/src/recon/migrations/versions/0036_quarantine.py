"""Quarantine of scripts from undecided hosts: host_decision + quarantined_asset (ADR-0010)

Revision ID: 0036_quarantine
Revises: 0035_run_capturing_state
Create Date: 2026-11-16

Two tenant tables, RLS like every other: the per-(session, host) decision record that moves by
compare-and-set, and the quarantined script rows. Quarantine is stored apart from ``run_asset``
(its own table and blob prefix), so no existing reader can see it.
"""

from __future__ import annotations

from alembic import op

from recon.db import models
from recon.db.base import Base

revision = "0036_quarantine"
down_revision = "0035_run_capturing_state"
branch_labels = None
depends_on = None

APP_ROLE = "recon_app"


def upgrade() -> None:
    bind = op.get_bind()
    # NOTE: 0001 runs create_all from the *live* models, so on a fresh DB / CI the tables already
    # exist; create_all is idempotent.
    Base.metadata.create_all(bind)
    for table in models.QUARANTINE_TABLES:
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
    for table in reversed(models.QUARANTINE_TABLES):
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"')
        op.drop_table(table)
