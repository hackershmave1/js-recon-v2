"""tenant_third_party_rule: the team's additions to the built-in third-party rules (ADR-0010, R2)

Revision ID: 0038_tenant_third_party_rule
Revises: 0037_host_decision_referenced
Create Date: 2026-11-16

One tenant table, RLS like every other. Additions only: nothing here can switch a built-in entry off.
"""

from __future__ import annotations

from alembic import op

from recon.db import models
from recon.db.base import Base

revision = "0038_tenant_third_party_rule"
down_revision = "0037_host_decision_referenced"
branch_labels = None
depends_on = None

APP_ROLE = "recon_app"


def upgrade() -> None:
    bind = op.get_bind()
    # NOTE: 0001 runs create_all from the *live* models, so on a fresh DB / CI the table already
    # exists; create_all is idempotent.
    Base.metadata.create_all(bind)
    for table in models.THIRD_PARTY_RULE_TABLES:
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
    for table in reversed(models.THIRD_PARTY_RULE_TABLES):
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"')
        op.drop_table(table)
