"""User login credential: app_user.password_hash

Revision ID: 0014_app_user_password
Revises: 0013_run_max_fetch_bytes
Create Date: 2026-08-14

Adds one nullable ``app_user.password_hash`` text column: the bcrypt hash that lets
a user authenticate via ``POST /auth/login`` (recon.auth). NULL = a user with no
local password (an unfinished seed, or a future Google-OAuth identity), who
therefore cannot log in with a password — existing rows read unchanged, no backfill.

Guarded with ``IF NOT EXISTS`` for the same reason as 0003/0009/0010/0011/0012/0013:
0001 runs a full ``Base.metadata.create_all`` from the LIVE model metadata, so a
from-scratch ``upgrade head`` already builds this column (the model now carries it)
before this revision runs. The guard makes the add a no-op on a fresh DB / CI while
still applying it to an older DB created before the model carried the column.

``app_user`` already carries FORCE row-level security from 0001, and RLS is
table-level, so the new nullable column needs no policy change; 0001's table-level
GRANTs + ``ALTER DEFAULT PRIVILEGES`` already cover new columns, so no re-grant.
"""

from __future__ import annotations

from alembic import op

revision = "0014_app_user_password"
down_revision = "0013_run_max_fetch_bytes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE app_user ADD COLUMN IF NOT EXISTS password_hash TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE app_user DROP COLUMN IF EXISTS password_hash")
