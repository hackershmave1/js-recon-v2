"""Add llm_call (one row per LLM API call) + session_threat_model.llm_call_id

Revision ID: 0031_llm_call
Revises: 0030_llm_preset_models
Create Date: 2026-11-03

(Revision id kept <=32 chars for Postgres' ``alembic_version`` column; it is 13.)
"""

from __future__ import annotations

from alembic import op

from recon.db import models
from recon.db.base import Base

revision = "0031_llm_call"
down_revision = "0030_llm_preset_models"
branch_labels = None
depends_on = None

APP_ROLE = "recon_app"


def upgrade() -> None:
    bind = op.get_bind()
    # NOTE: 0001 runs create_all from the *live* models, so on a fresh DB / CI the table and
    # column already exist; create_all and ADD COLUMN IF NOT EXISTS are both idempotent.
    Base.metadata.create_all(bind)
    op.execute('ALTER TABLE "session_threat_model" ADD COLUMN IF NOT EXISTS llm_call_id UUID')
    # ENABLE is required (FORCE alone never activates RLS), and the policy must read
    # app.current_tenant: the GUC tenant_session() sets (recon.db.base).
    for table in models.LLM_CALL_TABLES:
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
    op.execute('ALTER TABLE "session_threat_model" DROP COLUMN IF EXISTS llm_call_id')
    op.execute('DROP POLICY IF EXISTS tenant_isolation ON "llm_call"')
    op.drop_table("llm_call")
