"""D33-B add run.scan_suspected_secrets (opt-in low-confidence recall lane)

Revision ID: 0020_run_scan_suspected_secrets
Revises: 0019_run_asset_source_map_skip
Create Date: 2026-08-24

(Revision id kept <=32 chars for Postgres' ``alembic_version`` column; it is 31.)

Per-run opt-in for the D33-B "suspected secret" tier: when true, the analyze stage runs
Kingfisher at ``--confidence low`` and records the low-confidence-only hits as the
SECRET_SUSPECTED recall lane (~50% FP by design), kept out of the precision ``secret``
count and the REQ-D5 diff. NULL (every existing run + the default) reads as "off" — the
unchanged medium scan — so this is additive and backward-compatible.

NULLABLE (no default): mirrors ``max_fetch_bytes`` (0013) — a run knob where NULL means
"use the default", not a backfilled boolean fact like ``source_map_skipped`` (0019).

Guarded with ``IF NOT EXISTS`` for the same reason as 0003/0010/0013/0019: 0001 runs a
full ``Base.metadata.create_all`` from the LIVE model metadata, so a from-scratch
``upgrade head`` already builds ``run`` *with* this column before this revision runs — a
plain ``add_column`` would then fail DuplicateColumn on a fresh DB / CI. ``run`` already
has FORCE row-level security from 0005 and RLS is table-level, so a new column needs no
policy change.
"""

from __future__ import annotations

from alembic import op

revision = "0020_run_scan_suspected_secrets"
down_revision = "0019_run_asset_source_map_skip"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE run ADD COLUMN IF NOT EXISTS scan_suspected_secrets BOOLEAN")


def downgrade() -> None:
    op.execute("ALTER TABLE run DROP COLUMN IF EXISTS scan_suspected_secrets")
