"""Allow finding.type = 'endpoint_unresolved' (the unconfirmed lane, Tier 4)

Revision ID: 0015_finding_type_unresolved
Revises: 0014_app_user_password
Create Date: 2026-08-16

Widens the ``ck_finding_type`` CHECK to admit a fourth kind, ``endpoint_unresolved``
— a network sink we detect but whose URL isn't statically resolvable, surfaced as an
"unconfirmed" finding instead of being silently dropped (recon.findings.extract).

Drop-then-add rather than a guarded ``ADD``: a CHECK can't be widened in place. On a
FRESH DB / CI, 0001 already builds the constraint in its new 4-value form from the LIVE
model metadata (same reason 0003/0009-0014 guard their adds), so this revision drops
that 4-value constraint and re-adds an identical one — a net no-op. On an OLDER DB it
drops the 3-value constraint and installs the 4-value one. ``DROP ... IF EXISTS`` makes
the drop safe in both cases. A distinct type (not an attribute on ``endpoint``) is what
keeps the unconfirmed lane out of every ``type = 'endpoint'`` read model automatically.
"""

from __future__ import annotations

from alembic import op

revision = "0015_finding_type_unresolved"
down_revision = "0014_app_user_password"
branch_labels = None
depends_on = None

_ALLOWED = "('endpoint', 'secret', 'param', 'endpoint_unresolved')"
_PRIOR = "('endpoint', 'secret', 'param')"


def upgrade() -> None:
    op.execute("ALTER TABLE finding DROP CONSTRAINT IF EXISTS ck_finding_type")
    op.execute(f"ALTER TABLE finding ADD CONSTRAINT ck_finding_type CHECK (type IN {_ALLOWED})")


def downgrade() -> None:
    # Fails if any 'endpoint_unresolved' rows exist — expected: the type must be gone
    # before the constraint can be narrowed back.
    op.execute("ALTER TABLE finding DROP CONSTRAINT IF EXISTS ck_finding_type")
    op.execute(f"ALTER TABLE finding ADD CONSTRAINT ck_finding_type CHECK (type IN {_PRIOR})")
