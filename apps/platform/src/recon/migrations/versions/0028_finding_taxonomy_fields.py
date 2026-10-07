"""Add resolution + at_sink taxonomy columns to finding

Revision ID: 0028_finding_taxonomy_fields
Revises: 0027_session_threat_model
Create Date: 2026-09-08

Nullable columns — existing rows get NULL (pre-taxonomy runs had no classification).
New endpoint findings emitted after this migration carry the correct values from
RawEndpoint.resolution / RawEndpoint.at_sink; all other finding types stay NULL.
"""

from __future__ import annotations

from alembic import op

revision = "0028_finding_taxonomy_fields"
down_revision = "0027_session_threat_model"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS: 0001's create_all already built these columns from the live model on
    # a fresh DB / CI, so a bare add_column crashes there (DuplicateColumn).
    op.execute("ALTER TABLE finding ADD COLUMN IF NOT EXISTS resolution VARCHAR(16)")
    op.execute("ALTER TABLE finding ADD COLUMN IF NOT EXISTS at_sink BOOLEAN")


def downgrade() -> None:
    op.drop_column("finding", "at_sink")
    op.drop_column("finding", "resolution")
