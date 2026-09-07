"""Allow finding.type = 'postmessage_sink' | 'storage_sink' (client-side data-flow sinks)

Revision ID: 0025_finding_data_sinks
Revises: 0024_finding_endpoint_suspected
Create Date: 2026-09-07

(Revision id kept <=32 chars for Postgres' ``alembic_version`` column; it is 25.)

Widens the ``ck_finding_type`` CHECK to admit two new informational kinds:

- ``postmessage_sink`` — a ``addEventListener('message', …)`` call site: the postMessage
  listener attack surface (XSS-via-message). NOT a secret, NOT an endpoint; value stored
  CLEARTEXT; excluded from every ``type == 'endpoint'`` read model and the REQ-C2 coverage
  counters; outside the REQ-D5 diff.
- ``storage_sink`` — a ``localStorage``/``sessionStorage`` write or a ``document.cookie``
  assignment: persistence of user-controlled data. Same family/rules as ``postmessage_sink``.

Drop-then-add, exactly like 0024/0023/0022: a CHECK can't be widened in place. On a FRESH DB /
CI, 0001 already builds the constraint in its new 12-value form from the LIVE model metadata
(``ck_finding_type`` is ``_enum_check("type", FindingType)`` in db.models), so this revision
drops that 12-value constraint and re-adds an identical one — a net no-op. On an OLDER DB it
drops the 0024 10-value constraint and installs the 12-value one. ``DROP ... IF EXISTS`` makes
the drop safe in both cases. The value order matches the FindingType enum (so the re-added
constraint is textually identical to the fresh build): both new values are defined last in the
enum, appended after ``endpoint_suspected``.
"""

from __future__ import annotations

from alembic import op

revision = "0025_finding_data_sinks"
down_revision = "0024_finding_endpoint_suspected"
branch_labels = None
depends_on = None

_ALLOWED = (
    "('endpoint', 'secret', 'param', 'secret_suspected', "
    "'endpoint_unresolved', 'endpoint_generic', 'page_route', 'internal_ip', "
    "'graphql', 'endpoint_suspected', 'postmessage_sink', 'storage_sink')"
)
_PRIOR = (
    "('endpoint', 'secret', 'param', 'secret_suspected', "
    "'endpoint_unresolved', 'endpoint_generic', 'page_route', 'internal_ip', "
    "'graphql', 'endpoint_suspected')"
)


def upgrade() -> None:
    op.execute("ALTER TABLE finding DROP CONSTRAINT IF EXISTS ck_finding_type")
    op.execute(f"ALTER TABLE finding ADD CONSTRAINT ck_finding_type CHECK (type IN {_ALLOWED})")


def downgrade() -> None:
    # Fails if any 'postmessage_sink' or 'storage_sink' rows exist — expected: those types
    # must be gone before the constraint can be narrowed back.
    op.execute("ALTER TABLE finding DROP CONSTRAINT IF EXISTS ck_finding_type")
    op.execute(f"ALTER TABLE finding ADD CONSTRAINT ck_finding_type CHECK (type IN {_PRIOR})")
