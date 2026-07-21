"""slice-2 add run.source_map_ref (uploaded source map for Sourcemapper)

Revision ID: 0003_run_source_map_ref
Revises: 0002_findings
Create Date: 2026-07-21

Adds a nullable object-storage key for an optional uploaded ``.map`` so the
analyze stage can recover real per-source paths via Sourcemapper.

Guarded with ``IF NOT EXISTS`` on purpose: 0001/0002 build tables with
``Base.metadata.create_all()`` from the LIVE model metadata, so a from-scratch
``upgrade head`` already creates ``run`` *with* ``source_map_ref`` before this
revision runs — a plain ``add_column`` then fails with DuplicateColumn (this is
exactly what broke a fresh-DB migrate in CI). The guard makes the add a no-op on
such a fresh DB while still adding the column to an older DB created before the
model carried it. ``run`` already has FORCE row-level security from 0001 and RLS
is table-level, so a new nullable column needs no policy change.

NOTE: the underlying ``create_all``-vs-incremental-DDL seam is tracked debt
(docs/slice2-deferred-debt.md); freeze 0001 to a static snapshot before real
prod upgrades (M3), after which explicit ``add_column`` becomes safe again.
"""

from __future__ import annotations

from alembic import op

revision = "0003_run_source_map_ref"
down_revision = "0002_findings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE run ADD COLUMN IF NOT EXISTS source_map_ref TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE run DROP COLUMN IF EXISTS source_map_ref")
