"""phase-3 add run_asset.source_map_ref (per-asset uploaded/captured source map)

Revision ID: 0010_run_asset_source_map_ref
Revises: 0009_engagement
Create Date: 2026-08-05

Adds a nullable object-storage key for an optional per-asset ``.map`` (the Chrome
extension captures a bundle's source map post-auth and uploads it alongside the
JS). The analyze stage recovers real per-source paths from it — mirroring the
run-level ``run.source_map_ref`` (migration 0003), but per ``run_asset`` because a
capture batch is many assets each with their own map.

Guarded with ``IF NOT EXISTS`` for the same reason as 0003/0009: 0001 runs a full
``Base.metadata.create_all`` from the LIVE model metadata, so a from-scratch
``upgrade head`` already builds ``run_asset`` *with* ``source_map_ref`` before this
revision runs — a plain ``add_column`` would then fail DuplicateColumn on a fresh
DB / CI. The guard makes the add a no-op there while still adding the column to an
older DB created before the model carried it. ``run_asset`` already has FORCE
row-level security from 0005 and RLS is table-level, so a new nullable column needs
no policy change.
"""

from __future__ import annotations

from alembic import op

revision = "0010_run_asset_source_map_ref"
down_revision = "0009_engagement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE run_asset ADD COLUMN IF NOT EXISTS source_map_ref TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE run_asset DROP COLUMN IF EXISTS source_map_ref")
