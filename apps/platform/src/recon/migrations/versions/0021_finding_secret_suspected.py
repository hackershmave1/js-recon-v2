"""Allow finding.type = 'secret_suspected' (the D33-B opt-in low-confidence recall lane)

Revision ID: 0021_finding_secret_suspected
Revises: 0020_run_scan_suspected_secrets
Create Date: 2026-08-24

(Revision id kept <=32 chars for Postgres' ``alembic_version`` column; it is 29.)

Widens the ``ck_finding_type`` CHECK to admit a seventh kind, ``secret_suspected`` — an
opt-in, low-confidence secret sighting (~50% FP), a DISTINCT type from ``secret`` so it
stays out of the precision `secret` count and the REQ-D5 diff while reusing the secret
reveal/redaction machinery. See ``recon.domain.FindingType`` / DEBT D33.

Drop-then-add, exactly like 0018 (page_route): a CHECK can't be widened in place. On a
FRESH DB / CI, 0001 already builds the constraint in its new 7-value form from the LIVE
model metadata (``ck_finding_type`` is ``_enum_check("type", FindingType)`` in db.models),
so this revision drops that 7-value constraint and re-adds an identical one — a net no-op.
On an OLDER DB it drops the 0018 6-value constraint and installs the 7-value one.
``DROP ... IF EXISTS`` makes the drop safe in both cases. The value order matches the
FindingType enum (so the re-added constraint is textually identical to the fresh build).
"""

from __future__ import annotations

from alembic import op

revision = "0021_finding_secret_suspected"
down_revision = "0020_run_scan_suspected_secrets"
branch_labels = None
depends_on = None

_ALLOWED = (
    "('endpoint', 'secret', 'param', 'secret_suspected', "
    "'endpoint_unresolved', 'endpoint_generic', 'page_route')"
)
_PRIOR = "('endpoint', 'secret', 'param', 'endpoint_unresolved', 'endpoint_generic', 'page_route')"


def upgrade() -> None:
    op.execute("ALTER TABLE finding DROP CONSTRAINT IF EXISTS ck_finding_type")
    op.execute(f"ALTER TABLE finding ADD CONSTRAINT ck_finding_type CHECK (type IN {_ALLOWED})")


def downgrade() -> None:
    # Fails if any 'secret_suspected' rows exist — expected: the type must be gone before
    # the constraint can be narrowed back.
    op.execute("ALTER TABLE finding DROP CONSTRAINT IF EXISTS ck_finding_type")
    op.execute(f"ALTER TABLE finding ADD CONSTRAINT ck_finding_type CHECK (type IN {_PRIOR})")
