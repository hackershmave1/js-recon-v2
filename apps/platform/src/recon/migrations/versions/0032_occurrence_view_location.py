"""Add the exact view location (view_line/col/end_line/end_col) to finding_occurrence

Revision ID: 0032_occurrence_view_loc
Revises: 0031_llm_call
Create Date: 2026-11-03

Display-only columns: where the sighting sits in the text the Sources tab serves (1-based line,
0-based UTF-16 column, end exclusive — ``recon.findings.located``). Deliberately NOT the existing
``line``/``col``/``offset_*``: those feed ``occurrence_hash``, so changing their meaning would
duplicate every occurrence on a re-extract. Existing rows keep NULL ("approximate location").
"""

from __future__ import annotations

from alembic import op

revision = "0032_occurrence_view_loc"
down_revision = "0031_llm_call"
branch_labels = None
depends_on = None

_COLUMNS = ("view_line", "view_col", "view_end_line", "view_end_col")


def upgrade() -> None:
    # IF NOT EXISTS: 0001's create_all already built these columns from the live model on
    # a fresh DB / CI, so a bare add_column crashes there (DuplicateColumn).
    for column in _COLUMNS:
        op.execute(f"ALTER TABLE finding_occurrence ADD COLUMN IF NOT EXISTS {column} INTEGER")


def downgrade() -> None:
    for column in reversed(_COLUMNS):
        op.drop_column("finding_occurrence", column)
