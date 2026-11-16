"""host_decision: the crawl's referenced-not-fetched script hosts (ADR-0010, K1)

Revision ID: 0037_host_decision_referenced
Revises: 0036_quarantine
Create Date: 2026-11-16

A standard crawl lists the script hosts it saw but did not fetch as undecided hosts with no held
files; these two nullable columns remember which run saw them (Add re-crawls it) and a sample of
the script URLs. RLS is unchanged: host_decision is already a tenant table.
"""

from __future__ import annotations

from alembic import op

revision = "0037_host_decision_referenced"
down_revision = "0036_quarantine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NOTE: 0001 runs create_all from the live models, so on a fresh DB the columns already exist.
    op.execute(
        "ALTER TABLE host_decision ADD COLUMN IF NOT EXISTS referenced_run_id UUID "
        "REFERENCES run(id) ON DELETE SET NULL"
    )
    op.execute("ALTER TABLE host_decision ADD COLUMN IF NOT EXISTS referenced_urls JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE host_decision DROP COLUMN IF EXISTS referenced_urls")
    op.execute("ALTER TABLE host_decision DROP COLUMN IF EXISTS referenced_run_id")
