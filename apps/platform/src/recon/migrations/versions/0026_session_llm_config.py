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

import sqlalchemy as sa
from alembic import op

revision = "0026_session_llm_config"
down_revision = "0025_finding_data_sinks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "session_llm_config",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("session.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("encrypted_api_key", sa.Text, nullable=True),
        sa.Column("configured_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("session_id", name="uq_session_llm_config_session"),
        sa.CheckConstraint(
            "provider IN ('anthropic', 'openrouter', 'gemini')",
            name="ck_session_llm_config_provider",
        ),
    )
    op.create_index(
        "ix_session_llm_config_session",
        "session_llm_config",
        ["tenant_id", "session_id"],
    )

    # RLS: same pattern as every other tenant-scoped table.
    op.execute("ALTER TABLE session_llm_config FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON session_llm_config "
        "USING (tenant_id = current_setting('app.tenant_id')::uuid)"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON session_llm_config TO recon_app")


def downgrade() -> None:
    op.drop_index("ix_session_llm_config_session", table_name="session_llm_config")
    op.drop_table("session_llm_config")
