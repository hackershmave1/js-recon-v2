"""Add session_threat_model + threat — session-scoped LLM threat model

Revision ID: 0027_session_threat_model
Revises: 0026_session_llm_config
Create Date: 2026-09-07

``session_threat_model``: one row per session (UNIQUE on session_id), tracks
generation status (pending → running → done | failed) and token usage.

``threat``: N rows per threat model, each a structured threat with OWASP category,
severity, test steps (JSONB), and citations (finding_hash list, REQ-L4 verified
before storage). Ordered by ``rank`` for deterministic display.

RLS applied to both tables (FORCE + recon_app policy) — same pattern as every
other tenant-scoped table.
"""

from __future__ import annotations

from alembic import op

from recon.db import models
from recon.db.base import Base

revision = "0027_session_threat_model"
down_revision = "0026_session_llm_config"
branch_labels = None
depends_on = None

APP_ROLE = "recon_app"


def upgrade() -> None:
    bind = op.get_bind()
    # NOTE: 0001 runs create_all from the *live* models, so on a fresh DB / CI session_threat_model + threat
    # already exists and a bare op.create_table() crashes (DuplicateTable). create_all is
    # idempotent (builds only what's missing, incl. the model-declared indexes), so this
    # is a no-op there and adds the table(s) on an older dev DB. Same pattern as 0017.
    Base.metadata.create_all(bind)
    # 0001 only applies RLS to TENANT_SCOPED_TABLES, so these need it here. ENABLE is
    # required (FORCE alone never activates RLS), and the policy must read
    # app.current_tenant: the GUC tenant_session() sets (recon.db.base).
    for table in models.THREAT_MODEL_TABLES:
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
    for table in ("threat", "session_threat_model"):
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"')
    op.drop_index("ix_threat_model", table_name="threat")
    op.drop_table("threat")
    op.drop_index("ix_session_threat_model_tenant", table_name="session_threat_model")
    op.drop_table("session_threat_model")
