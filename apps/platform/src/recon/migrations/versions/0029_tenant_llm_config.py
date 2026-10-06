"""Add tenant_llm_config: team-wide LLM provider + encrypted API key

Revision ID: 0029_tenant_llm_config
Revises: 0028_finding_taxonomy_fields
Create Date: 2026-10-06

(Revision id kept <=32 chars for Postgres' ``alembic_version`` column; it is 22.)
"""

from __future__ import annotations

from alembic import op

from recon.db import models
from recon.db.base import Base

revision = "0029_tenant_llm_config"
down_revision = "0028_finding_taxonomy_fields"
branch_labels = None
depends_on = None

APP_ROLE = "recon_app"


def upgrade() -> None:
    bind = op.get_bind()
    # NOTE: 0001 runs create_all from the *live* models, so on a fresh DB / CI the table
    # already exists; create_all is idempotent (builds only what's missing), so this is a
    # no-op there and creates it on an existing DB. Same pattern as 0017 / 0026.
    Base.metadata.create_all(bind)
    # ENABLE is required (FORCE alone never activates RLS), and the policy must read
    # app.current_tenant: the GUC tenant_session() sets (recon.db.base).
    for table in models.TENANT_LLM_TABLES:
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
    op.execute('DROP POLICY IF EXISTS tenant_isolation ON "tenant_llm_config"')
    op.drop_table("tenant_llm_config")
