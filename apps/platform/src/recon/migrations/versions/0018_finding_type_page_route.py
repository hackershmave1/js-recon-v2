"""Allow finding.type = 'page_route' (the client-side navigation lane, Phase 2)

Revision ID: 0018_finding_type_page_route
Revises: 0017_technology
Create Date: 2026-08-17

Widens the ``ck_finding_type`` CHECK to admit a sixth kind, ``page_route`` — a client-side
navigation target (an ``href``/``src``/``action`` value, a nav sink such as
``location.assign``/``history.pushState``/``router.push``, or an off-sink absolute-URL
literal), surfaced as a distinct "page route" finding (recon.findings.extract). Distinct
from the API lanes (``endpoint``/``endpoint_unresolved``/``endpoint_generic``) so it is its
own category and never collides with them on one ``finding_hash``.

Drop-then-add, exactly like 0015/0016: a CHECK can't be widened in place. On a FRESH DB / CI,
0001 already builds the constraint in its new 6-value form from the LIVE model metadata
(``ck_finding_type`` is ``_enum_check("type", FindingType)`` in db.models), so this revision
drops that 6-value constraint and re-adds an identical one — a net no-op. On an OLDER DB it
drops the 5-value constraint and installs the 6-value one. ``DROP ... IF EXISTS`` makes the
drop safe in both cases. A distinct type (not an attribute on an existing kind) is what keeps
the page-route lane out of every ``type = 'endpoint'`` read model automatically. 0017 (the
run_technology table) did not touch this CHECK, so the prior state here is still 0016's 5.
"""

from __future__ import annotations

from alembic import op

revision = "0018_finding_type_page_route"
down_revision = "0017_technology"
branch_labels = None
depends_on = None

_ALLOWED = (
    "('endpoint', 'secret', 'param', 'endpoint_unresolved', 'endpoint_generic', 'page_route')"
)
_PRIOR = "('endpoint', 'secret', 'param', 'endpoint_unresolved', 'endpoint_generic')"


def upgrade() -> None:
    op.execute("ALTER TABLE finding DROP CONSTRAINT IF EXISTS ck_finding_type")
    op.execute(f"ALTER TABLE finding ADD CONSTRAINT ck_finding_type CHECK (type IN {_ALLOWED})")


def downgrade() -> None:
    # Fails if any 'page_route' rows exist — expected: the type must be gone before the
    # constraint can be narrowed back.
    op.execute("ALTER TABLE finding DROP CONSTRAINT IF EXISTS ck_finding_type")
    op.execute(f"ALTER TABLE finding ADD CONSTRAINT ck_finding_type CHECK (type IN {_PRIOR})")
