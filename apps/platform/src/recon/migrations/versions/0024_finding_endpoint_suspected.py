"""Allow finding.type = 'endpoint_suspected' (the promoted valid-path endpoint lane)

Revision ID: 0024_finding_endpoint_suspected
Revises: 0023_finding_type_graphql
Create Date: 2026-09-01

(Revision id kept <=32 chars for Postgres' ``alembic_version`` column; it is 31.)

Widens the ``ck_finding_type`` CHECK to admit a tenth kind, ``endpoint_suspected`` — a
suspected sink whose collapsed URL still carries a real path (>=1 static path segment),
normalized like a confirmed ENDPOINT and UNIONED into the "total endpoints" consumers
(headline count, OpenAPI export, probe, threat-model feed, classify/correlate), but a
DISTINCT type so the fail-closed default holds: an unaudited ``type == 'endpoint'`` read
model still excludes it (the ``hosts`` confirmed inventory + REQ-C2 ``coverage_pct`` +
REQ-D5 diff stay endpoint-only). See ``recon.domain.FindingType``.

Chains AFTER ``0023_finding_type_graphql`` (both this and GraphQL branched off
``0022_finding_internal_ip``; renumbered from 0023 -> 0024 to keep a single alembic head).
Drop-then-add, exactly like 0023/0022: a CHECK can't be widened in place. On a FRESH DB /
CI, 0001 already builds the constraint in its new 10-value form from the LIVE model metadata
(``ck_finding_type`` is ``_enum_check("type", FindingType)`` in db.models), so this revision
drops that 10-value constraint and re-adds an identical one — a net no-op. On an OLDER DB it
drops the 0023 9-value constraint (which already includes ``graphql``) and installs the
10-value one. ``DROP ... IF EXISTS`` makes the drop safe in both cases. The value order
matches the FindingType enum (so the re-added constraint is textually identical to the fresh
build): ``endpoint_suspected`` is defined last in the enum, so it is appended after ``graphql``.
"""

from __future__ import annotations

from alembic import op

revision = "0024_finding_endpoint_suspected"
down_revision = "0023_finding_type_graphql"
branch_labels = None
depends_on = None

_ALLOWED = (
    "('endpoint', 'secret', 'param', 'secret_suspected', "
    "'endpoint_unresolved', 'endpoint_generic', 'page_route', 'internal_ip', "
    "'graphql', 'endpoint_suspected')"
)
_PRIOR = (
    "('endpoint', 'secret', 'param', 'secret_suspected', "
    "'endpoint_unresolved', 'endpoint_generic', 'page_route', 'internal_ip', 'graphql')"
)


def upgrade() -> None:
    op.execute("ALTER TABLE finding DROP CONSTRAINT IF EXISTS ck_finding_type")
    op.execute(f"ALTER TABLE finding ADD CONSTRAINT ck_finding_type CHECK (type IN {_ALLOWED})")


def downgrade() -> None:
    # Fails if any 'endpoint_suspected' rows exist — expected: the type must be gone before
    # the constraint can be narrowed back.
    op.execute("ALTER TABLE finding DROP CONSTRAINT IF EXISTS ck_finding_type")
    op.execute(f"ALTER TABLE finding ADD CONSTRAINT ck_finding_type CHECK (type IN {_PRIOR})")
