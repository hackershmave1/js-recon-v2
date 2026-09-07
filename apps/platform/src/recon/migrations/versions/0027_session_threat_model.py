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

import sqlalchemy as sa
from alembic import op

revision = "0027_session_threat_model"
down_revision = "0026_session_llm_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "session_threat_model",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("session.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("provider", sa.String(32), nullable=True),
        sa.Column("model", sa.Text, nullable=True),
        sa.Column("prompt_tokens", sa.Integer, nullable=True),
        sa.Column("completion_tokens", sa.Integer, nullable=True),
        sa.Column("analysis_summary", sa.Text, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("session_id", name="uq_session_threat_model_session"),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'done', 'failed')",
            name="ck_session_threat_model_status",
        ),
    )
    op.create_index(
        "ix_session_threat_model_tenant",
        "session_threat_model",
        ["tenant_id", "session_id"],
    )

    op.create_table(
        "threat",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "threat_model_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("session_threat_model.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rank", sa.Integer, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("owasp_category", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column(
            "affected_endpoints",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "test_steps",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "citations",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.CheckConstraint(
            "severity IN ('critical', 'high', 'medium', 'low', 'info')",
            name="ck_threat_severity",
        ),
    )
    op.create_index("ix_threat_model", "threat", ["tenant_id", "threat_model_id"])

    for table in ("session_threat_model", "threat"):
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id')::uuid)"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO recon_app")


def downgrade() -> None:
    op.drop_index("ix_threat_model", table_name="threat")
    op.drop_table("threat")
    op.drop_index("ix_session_threat_model_tenant", table_name="session_threat_model")
    op.drop_table("session_threat_model")
