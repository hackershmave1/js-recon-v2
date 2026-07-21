"""slice-2 add run.source_map_ref (uploaded source map for Sourcemapper)

Revision ID: 0003_run_source_map_ref
Revises: 0002_findings
Create Date: 2026-07-21

Adds a nullable object-storage key for an optional uploaded ``.map`` so the
analyze stage can recover real per-source paths via Sourcemapper. Uses an
explicit ``add_column`` — unlike the table migrations, ``create_all`` never
ALTERs an existing table, so it would silently no-op this column. ``run`` already
carries FORCE row-level security from 0001, and RLS policies are table-level, so
a new nullable column needs no policy change.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_run_source_map_ref"
down_revision = "0002_findings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("run", sa.Column("source_map_ref", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("run", "source_map_ref")
