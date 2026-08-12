"""Per-run fetch-size cap override: run.max_fetch_bytes

Revision ID: 0013_run_max_fetch_bytes
Revises: 0012_run_crawl_mode
Create Date: 2026-08-12

Adds one nullable ``run.max_fetch_bytes`` integer column: a per-run override of the
default ``max_fetch_bytes`` fetch cap (edit-&-re-run), clamped to
``max_fetch_bytes_ceiling`` by ``config.clamp_fetch_bytes`` at read time. NULL = use
the global default, so existing rows read unchanged with no backfill.

Guarded with ``IF NOT EXISTS`` for the same reason as 0003/0009/0010/0011/0012: 0001
runs a full ``Base.metadata.create_all`` from the LIVE model metadata, so a
from-scratch ``upgrade head`` already builds this column (the model now carries it)
before this revision runs. The guard makes the add a no-op on a fresh DB / CI while
still applying it to an older DB created before the model carried the column.

``run`` already carries FORCE row-level security from 0001, and RLS is table-level,
so the new nullable column needs no policy change.
"""

from __future__ import annotations

from alembic import op

revision = "0013_run_max_fetch_bytes"
down_revision = "0012_run_crawl_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE run ADD COLUMN IF NOT EXISTS max_fetch_bytes INTEGER")


def downgrade() -> None:
    op.execute("ALTER TABLE run DROP COLUMN IF EXISTS max_fetch_bytes")
