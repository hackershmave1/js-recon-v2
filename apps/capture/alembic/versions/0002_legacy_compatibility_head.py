"""legacy compatibility head

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-16

Some development databases were stamped with Alembic revision ``0002``
before the migration history was consolidated into ``0001``.  This no-op
revision lets those databases be recognized by Alembic without changing
schema state.  New databases apply ``0001`` and then this compatibility
head.
"""

# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
