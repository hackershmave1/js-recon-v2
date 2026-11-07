"""Capture lifecycle: the CAPTURING run state + run.reanalyze_after

Revision ID: 0035_run_capturing_state
Revises: 0034_session_app_packages
Create Date: 2026-11-07

A capture (Start → End in the extension) is one run. "Analyze now" analyzes what has arrived and
the run rests in the non-terminal ``capturing`` state; later files re-analyze the SAME run once
uploads go quiet (``reanalyze_after``). Terminal states stay final.
"""

from __future__ import annotations

from alembic import op

revision = "0035_run_capturing_state"
down_revision = "0034_session_app_packages"
branch_labels = None
depends_on = None

_STATES = (
    "queued",
    "discovering",
    "fetching",
    "ingesting",
    "analyzing",
    "correlating",
    "paused",
    "done",
    "partial",
    "failed",
    "cancelled",
)


def _check(states: tuple[str, ...]) -> str:
    return "state IN (" + ", ".join(f"'{s}'" for s in states) + ")"


def upgrade() -> None:
    op.execute("ALTER TABLE run DROP CONSTRAINT IF EXISTS ck_run_state")
    op.execute(
        f"ALTER TABLE run ADD CONSTRAINT ck_run_state CHECK ({_check(_STATES + ('capturing',))})"
    )
    # IF NOT EXISTS: 0001's create_all already builds these from the live model on a fresh DB.
    op.execute("ALTER TABLE run ADD COLUMN IF NOT EXISTS reanalyze_after TIMESTAMPTZ")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_run_reanalyze_after ON run (reanalyze_after) "
        "WHERE reanalyze_after IS NOT NULL"
    )


def downgrade() -> None:
    # A capturing run has no pre-0035 meaning; it was mid-capture, so it reads as partial.
    op.execute("UPDATE run SET state = 'partial', ended_at = now() WHERE state = 'capturing'")
    op.execute("DROP INDEX IF EXISTS ix_run_reanalyze_after")
    op.execute("ALTER TABLE run DROP COLUMN IF EXISTS reanalyze_after")
    op.execute("ALTER TABLE run DROP CONSTRAINT IF EXISTS ck_run_state")
    op.execute(f"ALTER TABLE run ADD CONSTRAINT ck_run_state CHECK ({_check(_STATES)})")
