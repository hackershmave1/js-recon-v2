"""Add session_llm_config — per-session LLM provider + encrypted API key

Revision ID: 0026_session_llm_config
Revises: 0025_finding_data_sinks
Create Date: 2026-09-07

(Revision id kept <=32 chars for Postgres' ``alembic_version`` column; it is 22.)

Creates ``session_llm_config``: one row per session, holding the chosen LLM
provider (anthropic/openrouter/gemini), model name, and a Fernet-encrypted API
key ciphertext. RLS is applied (FORCE ROW LEVEL SECURITY + recon_app policy) in
the same pattern as every other tenant-scoped table (0001 / 0002 / 0004 etc.).
"""

from __future__ import annotations

from alembic import op

from recon.db import models
from recon.db.base import Base

revision = "0026_session_llm_config"
down_revision = "0025_finding_data_sinks"
branch_labels = None
depends_on = None

APP_ROLE = "recon_app"


def upgrade() -> None:
    bind = op.get_bind()
    # NOTE: 0001 runs create_all from the *live* models, so on a fresh DB / CI session_llm_config
    # already exists and a bare op.create_table() crashes (DuplicateTable). create_all is
    # idempotent (builds only what's missing, incl. the model-declared indexes), so this
    # is a no-op there and adds the table(s) on an older dev DB. Same pattern as 0017.
    Base.metadata.create_all(bind)
    # 0001 only applies RLS to TENANT_SCOPED_TABLES, so these need it here. ENABLE is
    # required (FORCE alone never activates RLS), and the policy must read
    # app.current_tenant: the GUC tenant_session() sets (recon.db.base).
    for table in models.LLM_TABLES:
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
    op.execute('DROP POLICY IF EXISTS tenant_isolation ON "session_llm_config"')
    op.drop_index("ix_session_llm_config_session", table_name="session_llm_config")
    op.drop_table("session_llm_config")
