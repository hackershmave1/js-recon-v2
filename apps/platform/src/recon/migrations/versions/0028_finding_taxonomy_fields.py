"""Add resolution + at_sink taxonomy columns to finding

Revision ID: 0028_finding_taxonomy_fields
Revises: 0027_session_threat_model
Create Date: 2026-09-08

Nullable columns — existing rows get NULL (pre-taxonomy runs had no classification).
New endpoint findings emitted after this migration carry the correct values from
RawEndpoint.resolution / RawEndpoint.at_sink; all other finding types stay NULL.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0028_finding_taxonomy_fields"
down_revision = "0027_session_threat_model"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("finding", sa.Column("resolution", sa.String(16), nullable=True))
    op.add_column("finding", sa.Column("at_sink", sa.Boolean, nullable=True))


def downgrade() -> None:
    op.drop_column("finding", "at_sink")
    op.drop_column("finding", "resolution")
