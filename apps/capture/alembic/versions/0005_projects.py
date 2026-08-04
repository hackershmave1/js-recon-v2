"""projects

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-03

Adds a first-class ``projects`` table (an engagement owning default recon settings)
and attaches sessions to it: ``project_id`` (nullable FK, SET NULL on project
delete), ``capture_config`` (snapshot of the non-scope config groups the session
captured under) and ``override_keys`` (config leaves the session overrode).
Existing sessions are left loose (project_id NULL). See api/app/models/project.py
and api/app/project_config.py.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("defaults", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column("sessions", sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_sessions_project_id", "sessions", "projects",
        ["project_id"], ["id"], ondelete="SET NULL",
    )
    op.add_column("sessions", sa.Column("capture_config", sa.JSON(), nullable=True))
    op.add_column("sessions", sa.Column("override_keys", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("sessions", "override_keys")
    op.drop_column("sessions", "capture_config")
    op.drop_constraint("fk_sessions_project_id", "sessions", type_="foreignkey")
    op.drop_column("sessions", "project_id")
    op.drop_table("projects")
