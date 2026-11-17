"""finding_occurrence.url_source: where in the page an unresolved URL comes from (#235)

Revision ID: 0040_occurrence_url_source
Revises: 0039_session_extid_write_once
Create Date: 2026-11-17

A display-only token (`{attr:data-url}`, `{stimulus:url}`, `{form:action}`) on each sighting.
Nullable, never part of occurrence_hash or finding_hash: existing rows read as NULL ("no page
source known") and finding identity / triage are unchanged.
"""

from __future__ import annotations

from alembic import op

revision = "0040_occurrence_url_source"
down_revision = "0039_session_extid_write_once"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS: migration 0001 builds the schema from the live models, so a fresh DB
    # already has this column (repo rule, .claude/rules/alembic-migrations.md).
    op.execute("ALTER TABLE finding_occurrence ADD COLUMN IF NOT EXISTS url_source TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE finding_occurrence DROP COLUMN IF EXISTS url_source")
