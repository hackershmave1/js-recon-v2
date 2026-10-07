"""Add tenant_llm_config.preset_models: admin overrides for the model presets

Revision ID: 0030_llm_preset_models
Revises: 0029_tenant_llm_config
Create Date: 2026-10-07

(Revision id kept <=32 chars for Postgres' ``alembic_version`` column; it is 22.)
The table's existing RLS policy and grants cover new columns.
"""

from __future__ import annotations

from alembic import op

revision = "0030_llm_preset_models"
down_revision = "0029_tenant_llm_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS: on a fresh DB, 0001/0029's create_all already built the column from
    # the live model, so a bare add_column would crash with DuplicateColumn.
    op.execute("ALTER TABLE tenant_llm_config ADD COLUMN IF NOT EXISTS preset_models JSONB")


def downgrade() -> None:
    op.drop_column("tenant_llm_config", "preset_models")
