"""Add session.app_packages: packages a researcher treats as app code

Revision ID: 0034_session_app_packages
Revises: 0033_identity_v3_rekey
Create Date: 2026-11-06

Third-party code (GLOSSARY) is hidden by default, detected by package path; a private package
(``@acme/api-client``) lives in node_modules too, so each session keeps the packages the researcher
treats as app code. Read-time only: nothing is re-analyzed when it changes.
"""

from __future__ import annotations

from alembic import op

revision = "0034_session_app_packages"
down_revision = "0033_identity_v3_rekey"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS: 0001's create_all already builds the column from the live model on a fresh DB.
    op.execute(
        "ALTER TABLE session ADD COLUMN IF NOT EXISTS app_packages JSONB NOT NULL DEFAULT '[]'::jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE session DROP COLUMN IF EXISTS app_packages")
