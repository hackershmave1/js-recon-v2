"""session.external_id is write-once (ADR-0012)

Revision ID: 0039_session_extid_write_once
Revises: 0038_tenant_third_party_rule
Create Date: 2026-11-16

The extension id is the write capability for a session's captures. ADR-0012 lets a workspace
session (NULL id) be given one exactly once; this trigger makes "once" a database rule, so no
future code path can change or clear an id that is already set. NULL -> value stays allowed.
"""

from __future__ import annotations

from alembic import op

revision = "0039_session_extid_write_once"
down_revision = "0038_tenant_third_party_rule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION session_external_id_write_once() RETURNS trigger AS $$
        BEGIN
            IF OLD.external_id IS NOT NULL AND NEW.external_id IS DISTINCT FROM OLD.external_id THEN
                RAISE EXCEPTION 'session.external_id is write-once' USING ERRCODE = '23000';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute("DROP TRIGGER IF EXISTS session_external_id_write_once ON session")
    op.execute(
        "CREATE TRIGGER session_external_id_write_once BEFORE UPDATE OF external_id ON session "
        "FOR EACH ROW EXECUTE FUNCTION session_external_id_write_once()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS session_external_id_write_once ON session")
    op.execute("DROP FUNCTION IF EXISTS session_external_id_write_once()")
