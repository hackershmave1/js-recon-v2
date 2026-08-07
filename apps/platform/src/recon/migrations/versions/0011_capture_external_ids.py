"""DEBT D1: capture-ingest idempotency keys (session.external_id, run.capture_external_id)

Revision ID: 0011_capture_external_ids
Revises: 0010_run_asset_source_map_ref
Create Date: 2026-08-07

Closes the get-or-create race on the extension->platform capture ingest path,
where concurrent batches for the same ext sessionId silently created duplicate
sessions/runs and orphaned captured post-auth JS (DEBT.md D1). Adds two dedicated,
nullable idempotency-key columns + a UNIQUE index on each; the capture router keys
get-or-create on them and self-heals on IntegrityError.

- ``session.external_id``       = the extension's sessionId for a capture session.
- ``run.capture_external_id``   = that sessionId while the run is the session's
                                  OPEN accumulator; NULL once analyze/start seals it.

Postgres UNIQUE is NULLS DISTINCT, so the (near-universal) NULL rows on non-capture
sessions/runs never collide — each index binds only capture rows.

Guarded with ``IF NOT EXISTS`` for the same reason as 0003/0009/0010: 0001 runs a
full ``Base.metadata.create_all`` from the LIVE model metadata, so a from-scratch
``upgrade head`` already builds these columns + unique indexes before this revision
runs. The guards make the adds no-ops on a fresh DB / CI while still applying them
to an older DB created before the model carried them. The index names match the
model's ``Index(...)`` exactly, so create_all and this migration never build two.

Backfill keys off ``authorized_by = 'chrome-extension-capture'`` — the marker set on
BOTH capture create paths (capture_router) and nowhere else — NOT the runtime
capture tenant name (unknown here). It is idempotent (``... IS NULL`` guard) and the
run predicate mirrors the pre-fix accumulator selection ("QUEUED + no Job") exactly,
so an in-flight open round keeps accumulating across the upgrade.

CAVEAT: if a deployment already carries pre-fix DUPLICATE capture rows, the unique
index creation fails loudly rather than silently continuing — surfacing prior damage
for manual cleanup. None are expected (capture ingest is new/flag-gated and the DBs
were wiped fresh), but a green migration on a dirty DB would be worse than a red one.

``session``/``run`` already carry FORCE row-level security from 0001, and RLS is
table-level, so the new nullable columns need no policy change.
"""

from __future__ import annotations

from alembic import op

revision = "0011_capture_external_ids"
down_revision = "0010_run_asset_source_map_ref"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE session ADD COLUMN IF NOT EXISTS external_id TEXT")
    op.execute("ALTER TABLE run ADD COLUMN IF NOT EXISTS capture_external_id TEXT")

    # Backfill existing capture rows by the reliable per-path marker.
    op.execute(
        "UPDATE session SET external_id = name "
        "WHERE authorized_by = 'chrome-extension-capture' "
        "AND name IS NOT NULL AND external_id IS NULL"
    )
    # Only OPEN accumulators (QUEUED + no Job) — mirrors _accumulating_run_id's
    # pre-fix selection, so an in-flight round keeps accumulating post-upgrade.
    op.execute(
        "UPDATE run SET capture_external_id = s.name "
        "FROM session s "
        "WHERE run.session_id = s.id "
        "AND s.authorized_by = 'chrome-extension-capture' "
        "AND run.state = 'queued' "
        "AND run.capture_external_id IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM job j WHERE j.run_id = run.id)"
    )

    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_session_tenant_external_id "
        "ON session (tenant_id, external_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_run_tenant_capture_external_id "
        "ON run (tenant_id, capture_external_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_run_tenant_capture_external_id")
    op.execute("DROP INDEX IF EXISTS uq_session_tenant_external_id")
    op.execute("ALTER TABLE run DROP COLUMN IF EXISTS capture_external_id")
    op.execute("ALTER TABLE session DROP COLUMN IF EXISTS external_id")
