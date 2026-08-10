"""Runtime-capture: run.crawl_mode selector

Revision ID: 0012_run_crawl_mode
Revises: 0011_capture_external_ids
Create Date: 2026-08-10

Adds one nullable ``run.crawl_mode`` column selecting the DISCOVER implementation:
NULL / "static" = the katana static crawl (every existing + non-capture run), and
"capture" = the in-process CDP headless-Chromium capture stage. Additive and
nullable, so existing rows read as static with no backfill.

Guarded with ``IF NOT EXISTS`` for the same reason as 0003/0009/0010/0011: 0001 runs
a full ``Base.metadata.create_all`` from the LIVE model metadata, so a from-scratch
``upgrade head`` already builds this column (the model now carries it) before this
revision runs. The guard makes the add a no-op on a fresh DB / CI while still
applying it to an older DB created before the model carried the column.

``run`` already carries FORCE row-level security from 0001, and RLS is table-level,
so the new nullable column needs no policy change.
"""

from __future__ import annotations

from alembic import op

revision = "0012_run_crawl_mode"
down_revision = "0011_capture_external_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE run ADD COLUMN IF NOT EXISTS crawl_mode TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE run DROP COLUMN IF EXISTS crawl_mode")
