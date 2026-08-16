"""Allow finding.type = 'endpoint_generic' (the generic-call lane, Tier 5)

Revision ID: 0016_finding_type_generic
Revises: 0015_finding_type_unresolved
Create Date: 2026-08-16

Widens the ``ck_finding_type`` CHECK to admit a fifth kind, ``endpoint_generic`` — a
verb-method call (``.get``/``.post``/…) on an unrecognised but HTTP-client-shaped
receiver, surfaced as a "generic call" finding (a SUSPECTED custom/untaught client,
recon.findings.extract). Distinct from ``endpoint_unresolved`` (a sink we DID detect
but couldn't resolve) so the two confidence tiers stay filterable and never collide on
one ``finding_hash``.

Drop-then-add, exactly like 0015: a CHECK can't be widened in place. On a FRESH DB / CI,
0001 already builds the constraint in its new 5-value form from the LIVE model metadata
(``ck_finding_type`` is ``_enum_check("type", FindingType)`` in db.models), so this
revision drops that 5-value constraint and re-adds an identical one — a net no-op. On an
OLDER DB it drops the 4-value constraint and installs the 5-value one. ``DROP ... IF
EXISTS`` makes the drop safe in both cases. A distinct type (not an attribute on an
existing kind) is what keeps the generic-call lane out of every ``type = 'endpoint'``
read model automatically.
"""

from __future__ import annotations

from alembic import op

revision = "0016_finding_type_generic"
down_revision = "0015_finding_type_unresolved"
branch_labels = None
depends_on = None

_ALLOWED = "('endpoint', 'secret', 'param', 'endpoint_unresolved', 'endpoint_generic')"
_PRIOR = "('endpoint', 'secret', 'param', 'endpoint_unresolved')"


def upgrade() -> None:
    op.execute("ALTER TABLE finding DROP CONSTRAINT IF EXISTS ck_finding_type")
    op.execute(f"ALTER TABLE finding ADD CONSTRAINT ck_finding_type CHECK (type IN {_ALLOWED})")


def downgrade() -> None:
    # Fails if any 'endpoint_generic' rows exist — expected: the type must be gone
    # before the constraint can be narrowed back.
    op.execute("ALTER TABLE finding DROP CONSTRAINT IF EXISTS ck_finding_type")
    op.execute(f"ALTER TABLE finding ADD CONSTRAINT ck_finding_type CHECK (type IN {_PRIOR})")
