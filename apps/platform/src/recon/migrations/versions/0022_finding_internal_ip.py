"""Allow finding.type = 'internal_ip' (the cleartext internal-IP info-disclosure lane)

Revision ID: 0022_finding_internal_ip
Revises: 0021_finding_secret_suspected
Create Date: 2026-08-24

(Revision id kept <=32 chars for Postgres' ``alembic_version`` column; it is 24.)

Widens the ``ck_finding_type`` CHECK to admit an eighth kind, ``internal_ip`` — a cleartext
internal-IP literal (info-disclosure), the first member of a NON-secret family. A DISTINCT
type from ``secret``: its value is stored + shown in CLEARTEXT (never hashed/redacted/
reveal-gated), it is counted SEPARATELY from secrets, and it stays out of the REQ-D5 diff.
See ``recon.domain.FindingType``.

Drop-then-add, exactly like 0021 (secret_suspected): a CHECK can't be widened in place. On a
FRESH DB / CI, 0001 already builds the constraint in its new 8-value form from the LIVE
model metadata (``ck_finding_type`` is ``_enum_check("type", FindingType)`` in db.models),
so this revision drops that 8-value constraint and re-adds an identical one — a net no-op.
On an OLDER DB it drops the 0021 7-value constraint and installs the 8-value one.
``DROP ... IF EXISTS`` makes the drop safe in both cases. The value order matches the
FindingType enum (so the re-added constraint is textually identical to the fresh build).
"""

from __future__ import annotations

from alembic import op

revision = "0022_finding_internal_ip"
down_revision = "0021_finding_secret_suspected"
branch_labels = None
depends_on = None

_ALLOWED = (
    "('endpoint', 'secret', 'param', 'secret_suspected', "
    "'endpoint_unresolved', 'endpoint_generic', 'page_route', 'internal_ip')"
)
_PRIOR = (
    "('endpoint', 'secret', 'param', 'secret_suspected', "
    "'endpoint_unresolved', 'endpoint_generic', 'page_route')"
)


def upgrade() -> None:
    op.execute("ALTER TABLE finding DROP CONSTRAINT IF EXISTS ck_finding_type")
    op.execute(f"ALTER TABLE finding ADD CONSTRAINT ck_finding_type CHECK (type IN {_ALLOWED})")


def downgrade() -> None:
    # Fails if any 'internal_ip' rows exist — expected: the type must be gone before
    # the constraint can be narrowed back.
    op.execute("ALTER TABLE finding DROP CONSTRAINT IF EXISTS ck_finding_type")
    op.execute(f"ALTER TABLE finding ADD CONSTRAINT ck_finding_type CHECK (type IN {_PRIOR})")
