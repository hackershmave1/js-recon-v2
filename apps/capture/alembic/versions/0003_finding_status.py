"""finding_status

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-19

Adds the ``finding_status`` table backing analyst triage persistence for the
RECON Workspace Findings view (UI-002 Phase 2). A row records the triage status
of one finding within a session, keyed by ``(session_id, fingerprint)`` where the
fingerprint is a stable client-computed identity (see
``api/app/models/finding_status.py``).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "finding_status",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="new"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("session_id", "fingerprint", name="uq_finding_status_session_fingerprint"),
    )
    op.create_index("idx_finding_status_session_id", "finding_status", ["session_id"])
    op.create_index("ix_finding_status_fingerprint", "finding_status", ["fingerprint"])


def downgrade() -> None:
    op.drop_index("ix_finding_status_fingerprint", table_name="finding_status")
    op.drop_index("idx_finding_status_session_id", table_name="finding_status")
    op.drop_table("finding_status")
