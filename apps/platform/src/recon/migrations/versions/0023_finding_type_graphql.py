"""Allow finding.type = 'graphql' (the GraphQL operation/fragment lane)

Revision ID: 0023_finding_type_graphql
Revises: 0022_finding_internal_ip
Create Date: 2026-09-01

Widens the ``ck_finding_type`` CHECK to admit a ninth kind, ``graphql`` — a GraphQL operation
(query/mutation/subscription) or fragment definition located statically in a JS bundle
(recon.findings.graphql_ops), surfaced as a first-class located finding + a dedicated workspace
tab. Distinct from the API lanes (``endpoint``/``endpoint_unresolved``/``endpoint_generic``), the
``page_route`` lane, and the secret/internal-IP lanes so it is its own category: a GraphQL op is
not an HTTP endpoint (it rides one POST to a ``/graphql`` route), so a distinct type keeps it out
of every ``type = 'endpoint'`` read model and the REQ-C2 coverage counters automatically, and it
never collides with them on one ``finding_hash``.

Drop-then-add, exactly like 0015/0016/0018/0021/0022: a CHECK can't be widened in place. On a
FRESH DB / CI, 0001 already builds the constraint in its new 9-value form from the LIVE model
metadata (``ck_finding_type`` is ``_enum_check("type", FindingType)`` in db.models), so this
revision drops that 9-value constraint and re-adds an identical one — a net no-op. On an OLDER DB
it drops the 8-value constraint and installs the 9-value one. ``DROP ... IF EXISTS`` makes the
drop safe in both cases.
"""

from __future__ import annotations

from alembic import op

revision = "0023_finding_type_graphql"
down_revision = "0022_finding_internal_ip"
branch_labels = None
depends_on = None

_ALLOWED = (
    "('endpoint', 'secret', 'param', 'secret_suspected', 'endpoint_unresolved', "
    "'endpoint_generic', 'page_route', 'internal_ip', 'graphql')"
)
_PRIOR = (
    "('endpoint', 'secret', 'param', 'secret_suspected', 'endpoint_unresolved', "
    "'endpoint_generic', 'page_route', 'internal_ip')"
)


def upgrade() -> None:
    op.execute("ALTER TABLE finding DROP CONSTRAINT IF EXISTS ck_finding_type")
    op.execute(f"ALTER TABLE finding ADD CONSTRAINT ck_finding_type CHECK (type IN {_ALLOWED})")


def downgrade() -> None:
    # Fails if any 'graphql' rows exist — expected: the type must be gone before the
    # constraint can be narrowed back.
    op.execute("ALTER TABLE finding DROP CONSTRAINT IF EXISTS ck_finding_type")
    op.execute(f"ALTER TABLE finding ADD CONSTRAINT ck_finding_type CHECK (type IN {_PRIOR})")
