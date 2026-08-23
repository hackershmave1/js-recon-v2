"""D32 add run_asset.source_map_skipped (a referenced .map was soft-missed)

Revision ID: 0019_run_asset_source_map_skip
Revises: 0018_finding_type_page_route
Create Date: 2026-08-23

(Revision id kept <=32 chars for Postgres' ``alembic_version`` column; the flag it adds
is ``source_map_skipped``.)

A crawled/captured asset can reference an external ``//# sourceMappingURL=`` whose
``.map`` we then FAIL to retrieve — most commonly because the real map is 3-6x the
bundle and trips the per-run byte cap, but also a 404/blocked/malformed map. Today
that soft-miss leaves ``source_map_ref`` null, which analyze cannot tell apart from a
bundle that simply had no map at all — so coverage reports ``source_map:"none"`` and
the whole gap is SILENT (REQ-D5). This flag makes "we saw a map reference but don't
have its contents" a first-class, honest per-asset fact: analyze reports
``source_map:"skipped"`` and the Overview surfaces a "Partial" coverage banner.

The fetch/analyze split spans two worker jobs, so the fact must ride the durable
``run_asset`` row (the only channel between them) — symmetric with ``source_map_ref``
(migration 0010), the success twin of this exact miss.

Guarded with ``IF NOT EXISTS`` for the same reason as 0003/0009/0010: 0001 runs a full
``Base.metadata.create_all`` from the LIVE model metadata, so a from-scratch
``upgrade head`` already builds ``run_asset`` *with* ``source_map_skipped`` before this
revision runs — a plain ``add_column`` would then fail DuplicateColumn on a fresh
DB / CI. The guard makes the add a no-op there while still adding the column to an
older DB. ``NOT NULL DEFAULT FALSE`` backfills existing rows. ``run_asset`` already has
FORCE row-level security from 0005 and RLS is table-level, so a new column needs no
policy change.
"""

from __future__ import annotations

from alembic import op

revision = "0019_run_asset_source_map_skip"
down_revision = "0018_finding_type_page_route"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE run_asset ADD COLUMN IF NOT EXISTS "
        "source_map_skipped BOOLEAN NOT NULL DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE run_asset DROP COLUMN IF EXISTS source_map_skipped")
